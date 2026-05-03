"""Deterministic-but-varying mock Google Ads dataset.

Seeded by the run date so light-mode threshold logic is exercisable end-to-end
without hitting the live API. Numbers are realistic for a $67/day OwnersHub
campaign targeting NSW strata committee keywords.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any


@dataclass
class KeywordRow:
    keyword: str
    match_type: str
    impressions: int
    clicks: int
    cost_aud: Decimal
    conversions: float
    conversion_value_aud: Decimal


@dataclass
class AdGroupRow:
    name: str
    impressions: int
    clicks: int
    cost_aud: Decimal
    conversions: float
    conversion_value_aud: Decimal
    keywords: list[KeywordRow] = field(default_factory=list)


@dataclass
class CampaignRow:
    name: str
    daily_budget_aud: Decimal
    impressions: int
    clicks: int
    cost_aud: Decimal
    conversions: float
    conversion_value_aud: Decimal
    ad_groups: list[AdGroupRow] = field(default_factory=list)


@dataclass
class AdsReport:
    start_date: date
    end_date: date
    days: int
    source: str  # "mock" or "live"
    campaigns: list[CampaignRow] = field(default_factory=list)

    def total_cost(self) -> Decimal:
        return sum((c.cost_aud for c in self.campaigns), Decimal("0"))

    def total_clicks(self) -> int:
        return sum(c.clicks for c in self.campaigns)

    def total_impressions(self) -> int:
        return sum(c.impressions for c in self.campaigns)

    def total_conversions(self) -> float:
        return sum(c.conversions for c in self.campaigns)

    def ctr(self) -> float:
        imp = self.total_impressions()
        return (self.total_clicks() / imp) if imp else 0.0

    def avg_cpc(self) -> Decimal:
        clicks = self.total_clicks()
        return (self.total_cost() / Decimal(clicks)) if clicks else Decimal("0")

    def conversion_rate(self) -> float:
        clicks = self.total_clicks()
        return (self.total_conversions() / clicks) if clicks else 0.0


_KEYWORD_TEMPLATES: list[tuple[str, str]] = [
    ("self managed strata", "PHRASE"),
    ("strata management software nsw", "PHRASE"),
    ("owners corporation portal", "BROAD"),
    ("strata levy management", "PHRASE"),
    ("strata compliance nsw", "BROAD"),
    ("cheap strata manager", "BROAD"),
    ("strata schemes management act", "EXACT"),
    ("body corporate software", "PHRASE"),
    ("ownerscorp self management", "BROAD"),
    ("strata committee tools", "BROAD"),
]

_AD_GROUPS: list[str] = [
    "Self-management - Generic",
    "Cost vs traditional",
    "Compliance - SSMA 2015",
    "Software comparison",
]


def _seed_for_date(end_date: date) -> int:
    """Stable seed per (year, ISO week) so daily light runs see slow drift."""
    year, week, _ = end_date.isocalendar()
    h = hashlib.sha256(f"{year}-W{week:02d}".encode()).hexdigest()
    return int(h[:8], 16)


def generate_mock_report(start_date: date, end_date: date, daily_budget_aud: float) -> AdsReport:
    """Build a realistic mock report. Deterministic per ISO-week."""
    days = (end_date - start_date).days + 1
    rng = random.Random(_seed_for_date(end_date))

    target_daily_spend = daily_budget_aud * rng.uniform(0.85, 0.98)
    total_cost = Decimal(f"{target_daily_spend * days:.2f}")
    avg_cpc = Decimal(f"{rng.uniform(2.20, 4.10):.2f}")
    total_clicks = int(total_cost / avg_cpc) if avg_cpc > 0 else 0
    ctr = rng.uniform(0.028, 0.055)
    total_impressions = int(total_clicks / ctr) if ctr > 0 else 0
    conv_rate = rng.uniform(0.018, 0.042)
    total_conversions = round(total_clicks * conv_rate, 2)
    avg_conv_value = Decimal(f"{rng.uniform(35, 95):.2f}")
    total_conv_value = (Decimal(str(total_conversions)) * avg_conv_value).quantize(Decimal("0.01"))

    ad_groups: list[AdGroupRow] = []
    remaining_imp = total_impressions
    remaining_clicks = total_clicks
    remaining_cost = total_cost
    remaining_conv = Decimal(str(total_conversions))
    remaining_value = total_conv_value

    for i, ag_name in enumerate(_AD_GROUPS):
        last = i == len(_AD_GROUPS) - 1
        share = rng.uniform(0.18, 0.32) if not last else 1.0
        ag_imp = remaining_imp if last else int(remaining_imp * share)
        ag_clicks = remaining_clicks if last else int(remaining_clicks * share)
        ag_cost = remaining_cost if last else (remaining_cost * Decimal(f"{share:.4f}")).quantize(Decimal("0.01"))
        ag_conv = float(remaining_conv) if last else round(float(remaining_conv) * share, 2)
        ag_value = remaining_value if last else (remaining_value * Decimal(f"{share:.4f}")).quantize(Decimal("0.01"))

        remaining_imp -= ag_imp
        remaining_clicks -= ag_clicks
        remaining_cost -= ag_cost
        remaining_conv -= Decimal(str(ag_conv))
        remaining_value -= ag_value

        keywords: list[KeywordRow] = []
        kw_pool = rng.sample(_KEYWORD_TEMPLATES, k=min(4, len(_KEYWORD_TEMPLATES)))
        kw_imp_left = ag_imp
        kw_click_left = ag_clicks
        kw_cost_left = ag_cost
        kw_conv_left = ag_conv
        kw_value_left = ag_value
        for j, (kw, mt) in enumerate(kw_pool):
            last_kw = j == len(kw_pool) - 1
            kw_share = rng.uniform(0.15, 0.45) if not last_kw else 1.0
            k_imp = kw_imp_left if last_kw else int(kw_imp_left * kw_share)
            k_clicks = kw_click_left if last_kw else int(kw_click_left * kw_share)
            k_cost = kw_cost_left if last_kw else (kw_cost_left * Decimal(f"{kw_share:.4f}")).quantize(Decimal("0.01"))
            k_conv = kw_conv_left if last_kw else round(kw_conv_left * kw_share, 2)
            k_value = kw_value_left if last_kw else (kw_value_left * Decimal(f"{kw_share:.4f}")).quantize(Decimal("0.01"))
            kw_imp_left -= k_imp
            kw_click_left -= k_clicks
            kw_cost_left -= k_cost
            kw_conv_left -= k_conv
            kw_value_left -= k_value
            keywords.append(KeywordRow(
                keyword=kw,
                match_type=mt,
                impressions=max(0, k_imp),
                clicks=max(0, k_clicks),
                cost_aud=max(Decimal("0"), k_cost),
                conversions=max(0.0, k_conv),
                conversion_value_aud=max(Decimal("0"), k_value),
            ))

        ad_groups.append(AdGroupRow(
            name=ag_name,
            impressions=max(0, ag_imp),
            clicks=max(0, ag_clicks),
            cost_aud=max(Decimal("0"), ag_cost),
            conversions=max(0.0, ag_conv),
            conversion_value_aud=max(Decimal("0"), ag_value),
            keywords=keywords,
        ))

    campaign = CampaignRow(
        name="OwnersHub - NSW Strata - Search",
        daily_budget_aud=Decimal(str(daily_budget_aud)),
        impressions=total_impressions,
        clicks=total_clicks,
        cost_aud=total_cost,
        conversions=total_conversions,
        conversion_value_aud=total_conv_value,
        ad_groups=ad_groups,
    )

    return AdsReport(
        start_date=start_date,
        end_date=end_date,
        days=days,
        source="mock",
        campaigns=[campaign],
    )


# ---------------------------------------------------------------------------
# Content-insights dataclasses — used by the content optimiser flow
# ---------------------------------------------------------------------------

@dataclass
class SearchTermRow:
    search_term: str
    campaign_name: str
    ad_group_name: str
    impressions: int
    clicks: int
    cost_aud: Decimal
    conversions: float
    ctr: float


@dataclass
class KeywordQualityRow:
    keyword: str
    match_type: str
    ad_group_name: str
    quality_score: int | None          # 1-10 (None if not available)
    landing_page_quality: str          # BELOW_AVERAGE | AVERAGE | ABOVE_AVERAGE
    ad_relevance: str                  # same scale
    expected_ctr: str                  # same scale
    impressions: int
    clicks: int
    cost_aud: Decimal
    conversions: float


@dataclass
class AdCopyRow:
    ad_group_name: str
    headlines: list[str]
    descriptions: list[str]
    final_url: str
    impressions: int
    clicks: int
    conversions: float
    ctr: float


@dataclass
class ContentInsightsReport:
    start_date: date
    end_date: date
    source: str                        # "mock" | "live"
    optimization_score: float | None   # 0-100
    search_terms: list[SearchTermRow]
    keyword_quality: list[KeywordQualityRow]
    ad_copies: list[AdCopyRow]


# ---------------------------------------------------------------------------
# Mock content-insights generator
# ---------------------------------------------------------------------------

_MOCK_SEARCH_TERMS: list[tuple[str, str]] = [
    # (query, ad_group) — ordered roughly by spend desc
    ("self managed strata nsw",             "Self-management - Generic"),
    ("strata committee responsibilities",    "Self-management - Generic"),
    ("strata compliance nsw",               "Compliance - SSMA 2015"),
    ("owners corporation management software", "Software comparison"),
    ("strata management software nsw",      "Software comparison"),
    ("cheap strata management",             "Cost vs traditional"),
    ("diy strata management",               "Self-management - Generic"),
    ("strata management without a manager", "Self-management - Generic"),
    ("strata schemes management act 2015",  "Compliance - SSMA 2015"),
    ("owners corporation fees nsw",         "Cost vs traditional"),
    ("body corporate software australia",   "Software comparison"),
    ("strata levy management",              "Self-management - Generic"),
    ("ownerscorp portal",                   "Software comparison"),
    ("strata software nsw",                 "Software comparison"),
    ("self managed owners corporation",     "Self-management - Generic"),
]

# clicks, conversions (low conv = high-traffic informational, high conv = high intent)
_MOCK_TERM_STATS: list[tuple[int, float, float]] = [
    # clicks, conversions, ctr
    (62, 0.8,  0.042),  # self managed strata nsw       — broad intent, low CVR
    (41, 0.1,  0.038),  # strata committee responsibilities — informational, near-zero CVR
    (28, 0.3,  0.031),  # strata compliance nsw          — research intent
    (24, 2.1,  0.044),  # owners corporation management software — high intent
    (22, 1.8,  0.041),  # strata management software nsw — high intent
    (20, 0.4,  0.029),  # cheap strata management        — price-sensitive
    (16, 0.2,  0.027),  # diy strata management          — early-stage research
    ( 9, 2.2,  0.048),  # strata management without a manager — very high intent
    (36, 0.1,  0.033),  # strata schemes management act 2015  — legislative research
    (18, 0.4,  0.030),  # owners corporation fees nsw     — cost curiosity
    (11, 1.0,  0.040),  # body corporate software australia — moderate intent
    (14, 0.6,  0.035),  # strata levy management          — moderate intent
    ( 7, 1.2,  0.043),  # ownerscorp portal               — branded, high intent
    ( 9, 1.5,  0.046),  # strata software nsw             — high intent
    (13, 1.1,  0.039),  # self managed owners corporation — moderate-high intent
]

_MOCK_KEYWORD_QUALITY: list[tuple[str, str, str, int, str, str, str]] = [
    # keyword, match_type, ad_group, quality_score, landing_page, ad_relevance, expected_ctr
    ("self managed strata",          "PHRASE", "Self-management - Generic",  6, "BELOW_AVERAGE", "AVERAGE",       "AVERAGE"),
    ("strata committee tools",       "BROAD",  "Self-management - Generic",  5, "BELOW_AVERAGE", "BELOW_AVERAGE", "AVERAGE"),
    ("strata compliance nsw",        "BROAD",  "Compliance - SSMA 2015",     7, "AVERAGE",       "AVERAGE",       "ABOVE_AVERAGE"),
    ("strata management software nsw","PHRASE","Software comparison",         8, "AVERAGE",       "ABOVE_AVERAGE", "ABOVE_AVERAGE"),
    ("strata schemes management act","EXACT",  "Compliance - SSMA 2015",     4, "BELOW_AVERAGE", "AVERAGE",       "BELOW_AVERAGE"),
    ("owners corporation portal",    "BROAD",  "Software comparison",         7, "ABOVE_AVERAGE", "AVERAGE",       "AVERAGE"),
    ("cheap strata manager",         "BROAD",  "Cost vs traditional",         6, "AVERAGE",       "AVERAGE",       "BELOW_AVERAGE"),
    ("body corporate software",      "PHRASE", "Software comparison",         7, "AVERAGE",       "AVERAGE",       "AVERAGE"),
    ("ownerscorp self management",   "BROAD",  "Self-management - Generic",  6, "AVERAGE",       "BELOW_AVERAGE", "AVERAGE"),
    ("strata levy management",       "PHRASE", "Self-management - Generic",  7, "AVERAGE",       "ABOVE_AVERAGE", "AVERAGE"),
]

_RSA_HEADLINES: list[list[str]] = [
    ["Self-Managed Strata NSW", "No Strata Manager Needed", "Save 15–20% on Strata Fees",
     "Register Interest Today", "Built for NSW Committees"],
    ["Cut Strata Costs by 15–20%", "Compare vs Traditional Manager", "Transparent Owners Corp Finances",
     "Free Pilot — NSW Strata", "Fixed Monthly Fee, No Surprises"],
    ["SSMA 2015 Compliance Built In", "Automate AGMs and Levy Notices", "NSW Legislation, Handled",
     "Compliance Without a Solicitor", "Strata Law Made Simple"],
    ["Strata Software for NSW", "All-in-One Owners Corp Platform", "Levy, AGM, Maintenance — One Place",
     "Compare Strata Software", "Try OwnersHub Free"],
]

_RSA_DESCRIPTIONS: list[list[str]] = [
    ["Automate levies, AGMs and compliance for your NSW owners corporation. Built for self-managed strata committees.",
     "Cut strata management fees by 15–20%. NSW-specific platform with full SSMA 2015 compliance automation."],
    ["Replace your strata manager with software. Transparent finances, automated compliance, flat monthly fee.",
     "NSW owners corporations save 15–20% vs traditional strata managers. No hidden fees. Your data, your control."],
    ["Full SSMA 2015 compliance automation. Levy schedules, AGM notices, maintenance logs — all in one platform.",
     "Stay compliant with NSW strata law without a strata manager. Automated notices and audit-ready reports."],
    ["All-in-one strata management software for NSW committees. Compare plans and register interest today.",
     "Manage levies, meetings and maintenance without a strata manager. Flat fee, no lock-in contract."],
]

_FINAL_URL = "https://ownershub.com.au/"


def generate_mock_content_insights(
    start_date: date, end_date: date, daily_budget_aud: float
) -> ContentInsightsReport:
    days = (end_date - start_date).days + 1
    rng = random.Random(_seed_for_date(end_date))
    avg_cpc = Decimal(f"{rng.uniform(2.20, 4.10):.2f}")

    search_terms: list[SearchTermRow] = []
    for (term, ag), (clicks, convs, ctr) in zip(_MOCK_SEARCH_TERMS, _MOCK_TERM_STATS):
        # scale clicks/cost to the lookback window with minor noise
        scaled_clicks = max(1, int(clicks * days / 14 * rng.uniform(0.85, 1.15)))
        cost = (avg_cpc * Decimal(scaled_clicks)).quantize(Decimal("0.01"))
        scaled_conv = round(convs * days / 14 * rng.uniform(0.80, 1.20), 2)
        search_terms.append(SearchTermRow(
            search_term=term,
            campaign_name="OwnersHub - NSW Strata - Search",
            ad_group_name=ag,
            impressions=int(scaled_clicks / ctr) if ctr else 0,
            clicks=scaled_clicks,
            cost_aud=cost,
            conversions=max(0.0, scaled_conv),
            ctr=round(ctr * rng.uniform(0.92, 1.08), 4),
        ))
    search_terms.sort(key=lambda r: r.cost_aud, reverse=True)

    keyword_quality: list[KeywordQualityRow] = []
    for kw, mt, ag, qs, lp, ar, ectr in _MOCK_KEYWORD_QUALITY:
        scaled_clicks = max(0, int(rng.randint(5, 40) * days / 14))
        cost = (avg_cpc * Decimal(scaled_clicks)).quantize(Decimal("0.01"))
        keyword_quality.append(KeywordQualityRow(
            keyword=kw,
            match_type=mt,
            ad_group_name=ag,
            quality_score=qs,
            landing_page_quality=lp,
            ad_relevance=ar,
            expected_ctr=ectr,
            impressions=int(scaled_clicks / rng.uniform(0.025, 0.055)),
            clicks=scaled_clicks,
            cost_aud=cost,
            conversions=round(scaled_clicks * rng.uniform(0.01, 0.05), 2),
        ))

    ad_copies: list[AdCopyRow] = []
    for i, ag_name in enumerate(_AD_GROUPS):
        ag_clicks = max(1, int(rng.randint(30, 120) * days / 14))
        ag_impr = int(ag_clicks / rng.uniform(0.025, 0.055))
        ad_copies.append(AdCopyRow(
            ad_group_name=ag_name,
            headlines=_RSA_HEADLINES[i],
            descriptions=_RSA_DESCRIPTIONS[i],
            final_url=_FINAL_URL,
            impressions=ag_impr,
            clicks=ag_clicks,
            conversions=round(ag_clicks * rng.uniform(0.015, 0.045), 2),
            ctr=round(ag_clicks / ag_impr, 4) if ag_impr else 0.0,
        ))

    return ContentInsightsReport(
        start_date=start_date,
        end_date=end_date,
        source="mock",
        optimization_score=round(rng.uniform(52.0, 74.0), 1),
        search_terms=search_terms,
        keyword_quality=keyword_quality,
        ad_copies=ad_copies,
    )


def content_insights_to_dict(report: ContentInsightsReport) -> dict[str, Any]:
    def _st(r: SearchTermRow) -> dict[str, Any]:
        conv_rate = round(r.conversions / r.clicks, 4) if r.clicks else 0.0
        return {
            "search_term": r.search_term,
            "ad_group": r.ad_group_name,
            "clicks": r.clicks,
            "cost_aud": str(r.cost_aud),
            "conversions": r.conversions,
            "ctr": r.ctr,
            "conversion_rate": conv_rate,
            "cost_per_conversion_aud": str(
                (r.cost_aud / Decimal(str(r.conversions))).quantize(Decimal("0.01"))
                if r.conversions > 0 else Decimal("0")
            ),
        }

    def _kq(r: KeywordQualityRow) -> dict[str, Any]:
        return {
            "keyword": r.keyword,
            "match_type": r.match_type,
            "ad_group": r.ad_group_name,
            "quality_score": r.quality_score,
            "landing_page_quality": r.landing_page_quality,
            "ad_relevance": r.ad_relevance,
            "expected_ctr": r.expected_ctr,
            "clicks": r.clicks,
            "cost_aud": str(r.cost_aud),
            "conversions": r.conversions,
        }

    def _ac(r: AdCopyRow) -> dict[str, Any]:
        return {
            "ad_group": r.ad_group_name,
            "headlines": r.headlines,
            "descriptions": r.descriptions,
            "final_url": r.final_url,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "conversions": r.conversions,
            "ctr": r.ctr,
        }

    below_avg = [
        r.keyword for r in report.keyword_quality
        if r.landing_page_quality == "BELOW_AVERAGE"
    ]

    return {
        "period": f"{report.start_date.isoformat()} to {report.end_date.isoformat()}",
        "source": report.source,
        "optimization_score": report.optimization_score,
        "optimization_score_note": (
            "Google's 0–100 estimate of account health vs best-practice recommendations"
        ),
        "keywords_with_below_average_landing_page": below_avg,
        "search_terms_top25_by_spend": [_st(r) for r in report.search_terms[:25]],
        "keyword_quality_scores": [_kq(r) for r in report.keyword_quality],
        "ad_copies": [_ac(r) for r in report.ad_copies],
    }


def report_to_dict(report: AdsReport) -> dict[str, Any]:
    """Serialize an AdsReport to a JSON-friendly dict (Decimals -> str)."""
    def _kw(k: KeywordRow) -> dict[str, Any]:
        return {
            "keyword": k.keyword,
            "match_type": k.match_type,
            "impressions": k.impressions,
            "clicks": k.clicks,
            "cost_aud": str(k.cost_aud),
            "conversions": k.conversions,
            "conversion_value_aud": str(k.conversion_value_aud),
        }

    def _ag(ag: AdGroupRow) -> dict[str, Any]:
        return {
            "name": ag.name,
            "impressions": ag.impressions,
            "clicks": ag.clicks,
            "cost_aud": str(ag.cost_aud),
            "conversions": ag.conversions,
            "conversion_value_aud": str(ag.conversion_value_aud),
            "keywords": [_kw(k) for k in ag.keywords],
        }

    def _camp(c: CampaignRow) -> dict[str, Any]:
        return {
            "name": c.name,
            "daily_budget_aud": str(c.daily_budget_aud),
            "impressions": c.impressions,
            "clicks": c.clicks,
            "cost_aud": str(c.cost_aud),
            "conversions": c.conversions,
            "conversion_value_aud": str(c.conversion_value_aud),
            "ad_groups": [_ag(ag) for ag in c.ad_groups],
        }

    return {
        "start_date": report.start_date.isoformat(),
        "end_date": report.end_date.isoformat(),
        "days": report.days,
        "source": report.source,
        "totals": {
            "impressions": report.total_impressions(),
            "clicks": report.total_clicks(),
            "cost_aud": str(report.total_cost()),
            "conversions": report.total_conversions(),
            "ctr": report.ctr(),
            "avg_cpc_aud": str(report.avg_cpc()),
            "conversion_rate": report.conversion_rate(),
        },
        "campaigns": [_camp(c) for c in report.campaigns],
    }
