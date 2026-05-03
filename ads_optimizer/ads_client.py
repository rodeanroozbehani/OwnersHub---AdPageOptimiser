"""Pluggable Google Ads client. Routes to live API or mock based on config.

Live mode is READ-ONLY: only GoogleAdsService.search (GAQL) is ever called.
No mutate operations are used or imported.

Prerequisites for live mode:
  - google-ads.yaml beside project root (chmod 600, see google-ads.yaml.example)
  - `ads.mode: live` and `ads.customer_id` set in config.yaml
  - run tools/oauth_bootstrap.py once to generate the refresh_token
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import ads_mock
from .ads_mock import (
    AdCopyRow, AdGroupRow, AdsReport, CampaignRow,
    ContentInsightsReport, KeywordQualityRow, KeywordRow, SearchTermRow,
)


logger = logging.getLogger(__name__)

_MICROS = Decimal("1000000")


def _micros_to_aud(micros: int | float) -> Decimal:
    return (Decimal(str(int(micros))) / _MICROS).quantize(Decimal("0.01"))


class AdsClient:
    def __init__(self, config: dict[str, Any]) -> None:
        ads_cfg = config["ads"]
        self.mode: str = ads_cfg["mode"]
        self.daily_budget_aud: float = float(ads_cfg["daily_budget_aud"])
        self.customer_id: str = str(ads_cfg.get("customer_id") or "").replace("-", "")
        self.login_customer_id: str = str(ads_cfg.get("login_customer_id") or "").replace("-", "")
        self.credentials_file: Path = Path(ads_cfg.get("credentials_file") or "google-ads.yaml")

    def fetch_metrics(self, start: date, end: date) -> AdsReport:
        if self.mode == "live":
            return self._fetch_live(start, end)
        return self._fetch_mock(start, end)

    def fetch_content_insights(self, start: date, end: date) -> ContentInsightsReport:
        """Return search-term, keyword-quality, ad-copy and optimisation-score data
        for the content optimiser. Routes to mock or live based on config."""
        if self.mode == "live":
            return self._fetch_content_insights_live(start, end)
        logger.info("ads_client: MOCK content insights (start=%s, end=%s)", start, end)
        return ads_mock.generate_mock_content_insights(start, end, self.daily_budget_aud)

    def _fetch_mock(self, start: date, end: date) -> AdsReport:
        logger.info("ads_client: MOCK mode (start=%s, end=%s)", start, end)
        return ads_mock.generate_mock_report(start, end, self.daily_budget_aud)

    # ------------------------------------------------------------------ #
    # Live Google Ads API  — read-only GAQL                               #
    # ------------------------------------------------------------------ #

    def _fetch_live(self, start: date, end: date) -> AdsReport:
        if not self.credentials_file.exists():
            raise FileNotFoundError(
                f"google-ads.yaml not found at: {self.credentials_file}\n"
                "1. Copy google-ads.yaml.example → google-ads.yaml\n"
                "2. Run tools/oauth_bootstrap.py to generate your refresh_token\n"
                "3. Fill in the remaining fields"
            )
        if not self.customer_id:
            raise ValueError("ads.mode is 'live' but ads.customer_id is empty in config.yaml")

        try:
            from google.ads.googleads.client import GoogleAdsClient
        except ImportError as exc:
            raise RuntimeError("google-ads not installed — run: pip install google-ads") from exc

        logger.info("ads_client: LIVE Google Ads (customer=%s, %s → %s)", self.customer_id, start, end)

        gads = GoogleAdsClient.load_from_storage(path=str(self.credentials_file))
        svc = gads.get_service("GoogleAdsService")
        date_filter = f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"

        # Each helper returns its own lookup dict; they share nothing mutable.
        campaigns, camp_by_name = _query_campaigns(svc, self.customer_id, date_filter)
        if not campaigns:
            logger.warning(
                "No campaign data for %s → %s. Check customer_id and account activity.",
                start, end,
            )
            return AdsReport(
                start_date=start, end_date=end,
                days=(end - start).days + 1,
                source="live", campaigns=[],
            )

        ag_by_id = _query_ad_groups(svc, self.customer_id, date_filter, campaigns)
        _query_keywords(svc, self.customer_id, date_filter, ag_by_id)

        return AdsReport(
            start_date=start, end_date=end,
            days=(end - start).days + 1,
            source="live",
            campaigns=list(campaigns.values()),
        )

    def _fetch_content_insights_live(self, start: date, end: date) -> ContentInsightsReport:
        """Live path: pull optimisation score, search terms, keyword quality and ad copy."""
        if not self.credentials_file.exists():
            raise FileNotFoundError(
                f"google-ads.yaml not found at: {self.credentials_file}"
            )
        if not self.customer_id:
            raise ValueError("ads.mode is 'live' but ads.customer_id is empty in config.yaml")

        try:
            from google.ads.googleads.client import GoogleAdsClient
        except ImportError as exc:
            raise RuntimeError("google-ads not installed — run: pip install google-ads") from exc

        logger.info(
            "ads_client: LIVE content insights (customer=%s, %s → %s)",
            self.customer_id, start, end,
        )
        gads = GoogleAdsClient.load_from_storage(path=str(self.credentials_file))
        svc = gads.get_service("GoogleAdsService")
        date_filter = f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"

        opt_score = _query_optimization_score(svc, self.customer_id)
        search_terms = _query_search_terms(svc, self.customer_id, date_filter)
        keyword_quality = _query_keyword_quality(svc, self.customer_id, date_filter)
        ad_copies = _query_ad_copies(svc, self.customer_id, date_filter)

        return ContentInsightsReport(
            start_date=start,
            end_date=end,
            source="live",
            optimization_score=opt_score,
            search_terms=search_terms,
            keyword_quality=keyword_quality,
            ad_copies=ad_copies,
        )


# ------------------------------------------------------------------ #
# GAQL helpers — module-level so they're easy to unit-test separately  #
# ------------------------------------------------------------------ #

def _query_campaigns(
    svc: Any, customer_id: str, date_filter: str
) -> tuple[dict[int, CampaignRow], dict[str, int]]:
    """Return (campaign_id → CampaignRow, campaign_name → campaign_id).

    Omitting segments.date from SELECT causes the API to return one row per
    campaign with metrics aggregated across the entire date-filter window.
    """
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign_budget.amount_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.conversion_value,
            metrics.ctr
        FROM campaign
        WHERE {date_filter}
          AND campaign.status != 'REMOVED'
        ORDER BY campaign.id
    """
    campaigns: dict[int, CampaignRow] = {}
    name_to_id: dict[str, int] = {}
    for row in svc.search(customer_id=customer_id, query=query):
        camp = row.campaign
        m = row.metrics
        campaigns[camp.id] = CampaignRow(
            name=camp.name,
            daily_budget_aud=_micros_to_aud(row.campaign_budget.amount_micros),
            impressions=int(m.impressions),
            clicks=int(m.clicks),
            cost_aud=_micros_to_aud(m.cost_micros),
            conversions=round(float(m.conversions), 2),
            conversion_value_aud=Decimal(str(round(float(m.conversion_value), 2))),
            ad_groups=[],
        )
        name_to_id[camp.name] = camp.id
    logger.debug("ads_client: %d campaigns", len(campaigns))
    return campaigns, name_to_id


def _query_ad_groups(
    svc: Any,
    customer_id: str,
    date_filter: str,
    campaigns: dict[int, CampaignRow],
) -> dict[int, AdGroupRow]:
    """Populate campaigns[*].ad_groups in-place. Return ag_id → AdGroupRow map."""
    query = f"""
        SELECT
            campaign.id,
            ad_group.id,
            ad_group.name,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.conversion_value
        FROM ad_group
        WHERE {date_filter}
          AND campaign.status != 'REMOVED'
          AND ad_group.status != 'REMOVED'
        ORDER BY ad_group.id
    """
    ag_by_id: dict[int, AdGroupRow] = {}
    for row in svc.search(customer_id=customer_id, query=query):
        camp_id = row.campaign.id
        ag = row.ad_group
        m = row.metrics
        ag_row = AdGroupRow(
            name=ag.name,
            impressions=int(m.impressions),
            clicks=int(m.clicks),
            cost_aud=_micros_to_aud(m.cost_micros),
            conversions=round(float(m.conversions), 2),
            conversion_value_aud=Decimal(str(round(float(m.conversion_value), 2))),
            keywords=[],
        )
        ag_by_id[ag.id] = ag_row
        if camp_id in campaigns:
            campaigns[camp_id].ad_groups.append(ag_row)
    logger.debug("ads_client: %d ad groups", len(ag_by_id))
    return ag_by_id


def _query_keywords(
    svc: Any,
    customer_id: str,
    date_filter: str,
    ag_by_id: dict[int, AdGroupRow],
) -> None:
    """Populate ag.keywords in-place for every ad group. Best-effort (logs on failure)."""
    query = f"""
        SELECT
            ad_group.id,
            ad_group_criterion.keyword.text,
            ad_group_criterion.keyword.match_type,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.conversion_value
        FROM keyword_view
        WHERE {date_filter}
          AND campaign.status != 'REMOVED'
          AND ad_group.status != 'REMOVED'
          AND ad_group_criterion.status != 'REMOVED'
        ORDER BY metrics.cost_micros DESC
    """
    total = 0
    for row in svc.search(customer_id=customer_id, query=query):
        ag_id = row.ad_group.id
        if ag_id not in ag_by_id:
            continue
        kw = row.ad_group_criterion.keyword
        m = row.metrics
        try:
            match_type_str = kw.match_type.name  # proto-plus enum → "BROAD" / "PHRASE" / "EXACT"
        except AttributeError:
            match_type_str = str(kw.match_type)

        ag_by_id[ag_id].keywords.append(KeywordRow(
            keyword=kw.text,
            match_type=match_type_str,
            impressions=int(m.impressions),
            clicks=int(m.clicks),
            cost_aud=_micros_to_aud(m.cost_micros),
            conversions=round(float(m.conversions), 2),
            conversion_value_aud=Decimal(str(round(float(m.conversion_value), 2))),
        ))
        total += 1
    logger.debug("ads_client: %d keywords", total)


# ------------------------------------------------------------------ #
# Content-insights GAQL helpers                                        #
# ------------------------------------------------------------------ #

def _query_optimization_score(svc: Any, customer_id: str) -> float | None:
    """Return the campaign-level optimisation score (0-100), averaged across active campaigns."""
    query = """
        SELECT campaign.name, campaign.optimization_score
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.optimization_score > 0
    """
    scores: list[float] = []
    try:
        for row in svc.search(customer_id=customer_id, query=query):
            scores.append(float(row.campaign.optimization_score) * 100)
    except Exception as exc:
        logger.warning("ads_client: optimization_score query failed: %s", exc)
        return None
    return round(sum(scores) / len(scores), 1) if scores else None


def _query_search_terms(
    svc: Any, customer_id: str, date_filter: str
) -> list[SearchTermRow]:
    """Return top 25 actual search queries (search_term_view), sorted by cost desc."""
    query = f"""
        SELECT
            search_term_view.search_term,
            campaign.name,
            ad_group.name,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.ctr
        FROM search_term_view
        WHERE {date_filter}
          AND campaign.status != 'REMOVED'
        ORDER BY metrics.cost_micros DESC
        LIMIT 25
    """
    rows: list[SearchTermRow] = []
    try:
        for row in svc.search(customer_id=customer_id, query=query):
            m = row.metrics
            rows.append(SearchTermRow(
                search_term=row.search_term_view.search_term,
                campaign_name=row.campaign.name,
                ad_group_name=row.ad_group.name,
                impressions=int(m.impressions),
                clicks=int(m.clicks),
                cost_aud=_micros_to_aud(m.cost_micros),
                conversions=round(float(m.conversions), 2),
                ctr=round(float(m.ctr), 4),
            ))
    except Exception as exc:
        logger.warning("ads_client: search_term_view query failed: %s", exc)
    logger.debug("ads_client: %d search terms", len(rows))
    return rows


def _query_keyword_quality(
    svc: Any, customer_id: str, date_filter: str
) -> list[KeywordQualityRow]:
    """Return keyword quality scores including per-keyword landing page quality signal."""
    query = f"""
        SELECT
            ad_group_criterion.keyword.text,
            ad_group_criterion.keyword.match_type,
            ad_group.name,
            ad_group_criterion.quality_info.quality_score,
            metrics.historical_landing_page_quality_score,
            metrics.historical_creative_quality_score,
            metrics.historical_expected_ctr,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions
        FROM keyword_view
        WHERE {date_filter}
          AND campaign.status != 'REMOVED'
          AND ad_group.status != 'REMOVED'
          AND ad_group_criterion.status != 'REMOVED'
        ORDER BY metrics.cost_micros DESC
        LIMIT 30
    """
    rows: list[KeywordQualityRow] = []

    def _enum_name(val: Any) -> str:
        try:
            return val.name
        except AttributeError:
            return str(val)

    try:
        for row in svc.search(customer_id=customer_id, query=query):
            crit = row.ad_group_criterion
            m = row.metrics
            qs_raw = crit.quality_info.quality_score
            qs = int(qs_raw) if qs_raw else None
            rows.append(KeywordQualityRow(
                keyword=crit.keyword.text,
                match_type=_enum_name(crit.keyword.match_type),
                ad_group_name=row.ad_group.name,
                quality_score=qs,
                landing_page_quality=_enum_name(m.historical_landing_page_quality_score),
                ad_relevance=_enum_name(m.historical_creative_quality_score),
                expected_ctr=_enum_name(m.historical_expected_ctr),
                impressions=int(m.impressions),
                clicks=int(m.clicks),
                cost_aud=_micros_to_aud(m.cost_micros),
                conversions=round(float(m.conversions), 2),
            ))
    except Exception as exc:
        logger.warning("ads_client: keyword quality query failed: %s", exc)
    logger.debug("ads_client: %d keyword quality rows", len(rows))
    return rows


def _query_ad_copies(
    svc: Any, customer_id: str, date_filter: str
) -> list[AdCopyRow]:
    """Return RSA headlines, descriptions and final URLs per ad group."""
    query = f"""
        SELECT
            ad_group.name,
            ad_group_ad.ad.responsive_search_ad.headlines,
            ad_group_ad.ad.responsive_search_ad.descriptions,
            ad_group_ad.ad.final_urls,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.ctr
        FROM ad_group_ad
        WHERE {date_filter}
          AND campaign.status != 'REMOVED'
          AND ad_group.status != 'REMOVED'
          AND ad_group_ad.status != 'REMOVED'
          AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
        ORDER BY metrics.impressions DESC
    """
    rows: list[AdCopyRow] = []
    try:
        for row in svc.search(customer_id=customer_id, query=query):
            ad = row.ad_group_ad.ad
            rsa = ad.responsive_search_ad
            headlines = [asset.text for asset in rsa.headlines if asset.text]
            descriptions = [asset.text for asset in rsa.descriptions if asset.text]
            final_urls = list(ad.final_urls) if ad.final_urls else []
            m = row.metrics
            rows.append(AdCopyRow(
                ad_group_name=row.ad_group.name,
                headlines=headlines,
                descriptions=descriptions,
                final_url=final_urls[0] if final_urls else "",
                impressions=int(m.impressions),
                clicks=int(m.clicks),
                conversions=round(float(m.conversions), 2),
                ctr=round(float(m.ctr), 4),
            ))
    except Exception as exc:
        logger.warning("ads_client: ad copy query failed: %s", exc)
    logger.debug("ads_client: %d ad copy rows", len(rows))
    return rows
