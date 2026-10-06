# Source register: insurance and housing inputs

Audit for issue #1. All URLs were retrieved on **2026-10-05** with a scripted HTTP GET. File
sizes are in bytes. Hashes are SHA-256 of the bytes as served on that date. Publishers replace
files in place without changing the URL. If a hash changes, treat the file as a new vintage.

`docs/coverage_matrix.csv` has the year-by-year coverage for each series. In the matrix,
`available` means a full-year value was published, `suppressed` means the publisher withholds
the cell, and `missing` means no published value was found. Partial years (for example 2026)
are `missing`.

Status key:

- **confirmed downloadable**: fetched, opened and parsed. File size and hash recorded.
- **proposed**: a candidate input that was not downloaded (login, key, or bot wall).
- **unavailable**: searched for, and not published at the needed geography or years.

Nevada County ZIPs referred to below: 95945 and 95949 (Grass Valley), 95959 (Nevada City),
95946 (Penn Valley), 95975 (Rough and Ready), 95960, 95977, 95986, 95712, 95924, 95724, 95728,
96111, 96160, 96161, 96162 (Truckee area).

## Summary

| ID | Source | Geography | Years (verified) | Status |
|----|--------|-----------|------------------|--------|
| S1 | CDI Residential Property Insurance Report, Part I | state | 2008-2023 (xlsx), 2001-2017 (PDF archive) | confirmed downloadable |
| S2 | CDI SB 824 Wildfire Risk Information data (Part II) | ZIP | 2018-2023 | confirmed downloadable |
| S3 | CDI new/renewed/nonrenewed policies by ZIP | ZIP | 2015-2023 (two vintages) | confirmed downloadable |
| S4 | CDI new/renewed/nonrenewed policies by county, incl. FAIR Plan | county | 2015-2023 (two vintages) | confirmed downloadable |
| S5 | CDI fire and earthquake policy counts by county | county | 2017, 2019, 2021, 2023 | confirmed downloadable |
| S6 | CDI share of dwelling units insured by FAIR Plan, by ZIP | ZIP | 2022 only | confirmed downloadable |
| S7 | CDI fact sheets on residential policies and the FAIR Plan | state, county groups | 2015-2023 | confirmed downloadable |
| S8 | CDI Appendix C, dwelling units in high/very high wildfire risk | county | one vintage (dwelling units as of 2015-01-01) | confirmed downloadable |
| S9 | California FAIR Plan policies in force and exposure by ZIP/county | ZIP, county | FY2021-FY2025 (30 Sep) | confirmed downloadable |
| S10 | California FAIR Plan policies/premium/exposure by category | ZIP | snapshot 2026-06-30 | confirmed downloadable |
| S11 | Zillow ZHVI, ZIP | ZIP | 2000/2001-2025 full years; 2026 through August | confirmed downloadable |
| S12 | Zillow ZHVI, city (used by `fetch_data.py`) | city | 2001-2025 full years; 2026 through August | confirmed downloadable |
| S13 | Census ACS 5-year, ZCTA | ZCTA | 2007-2011 through 2020-2024 | confirmed downloadable (2023, 2024 B25077 fetched) |
| S14 | Census ZCTA relationship files | ZCTA to county, ZCTA to ZCTA | 2010 and 2020 vintages | confirmed downloadable |
| S15 | HUD USPS ZIP crosswalk | ZIP to ZCTA/county/tract | not verified | proposed (requires login) |
| S16 | FRED series used by `fetch_data.py` | national, county | see entry | confirmed downloadable |
| S17 | Census population estimates used by `fetch_data.py` | county | 2000-2025 | confirmed downloadable |
| U1 | ZIP-level admitted homeowners premium before 2018 | ZIP | none | unavailable |
| U2 | Any CDI ZIP-level insurance data for 2024 or later | ZIP | none | unavailable |
| U3 | FAIR Plan ZIP policy counts before FY2021 | ZIP | none | unavailable |
| U4 | Surplus lines policies below state level | ZIP, county | none | unavailable |

---

## S1. CDI Residential Property Insurance Report, Part I (premium and exposure summary)

- **Landing page**: https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/ (HTTP 200)
- **Files**:

  | File | URL | Bytes | SHA-256 | Last-Modified |
  |------|-----|-------|---------|---------------|
  | Part I xlsx | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/A-Part-I-Data-Summary.xlsx | 80004 | 09df35ad84d73736f60873e053990ce9dc567fddf076487a30500a46f8308e65 | 2025-07-09 |
  | Homeowners archive PDF | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/A-Homeowners-2_v1-2.pdf | 250242 | c540bd4fc4b430d216a55fb6d67c9b8e8ef8c558f7f1b271184195fa2d298234 | 2019-07-02 |
  | Dwelling fire owner-occupied PDF | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/A-DF-Owner-Occupied_v1.pdf | 252200 | 9a420af3a1a2f3a7d26bf6aca571788b03164e7ae21fa18bfd83afc12b98d7ff | 2019-07-02 |
  | Dwelling fire tenant-occupied PDF | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/A-DF-Rental-2_v1.pdf | 147145 | 62879ea4cad1b9ad3654e9057b16b53c08f88a8c51113124d942e0e59f804ba6 | 2019-07-02 |
  | Mobile homes PDF | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/A-Mobile-Homes_v1.pdf | 249165 | 719818f4b93181d6657bfa981de42dac4698349a8585b523781662b8d2d3febb | 2019-07-02 |
  | Condominium PDF | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/A-Condo_v1.pdf | 269425 | 46acf8fe43650e7d096e8ecf5bb3c9dd3413cdae442d79e65353583cb59f71ee | 2019-07-02 |
  | Renters PDF | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/A-Renters_v1.pdf | 254538 | 5264b3cbe2eba64452da672f0b5afb37095f47e8837f4fe8d1c98216aacb5695 | 2019-07-02 |

- **Period**: xlsx experience years 2008-2023. PDF archive experience years 2001-2017.
- **Geography**: state only. There is no county or ZIP breakdown in Part I.
- **Policy types**: homeowners multi-peril, dwelling fire owner-occupied, dwelling fire tenant-occupied,
  mobile homes, renters, condominium unit owners. Admitted insurers only. FAIR Plan not included.
- **Units**: written premium (USD, nominal), written exposure (house-years), average written premium (USD).
- **Layout by vintage**: xlsx has two pivot-table sheets (by policy form, year and range of insurance;
  by policy form and year). The values are in the pivot cache (1,024 records), not in the sheet cells,
  so read `xl/pivotCache/pivotCacheRecords1.xml` or open in Excel. Range-of-insurance bands differ by
  form (10 bands up to "100,000 and over", 11 bands up to "500,000 and over"). The PDF archive is one
  page per policy form, one row per year.
- **Suppression**: none observed. Some records have negative written premium (minimum -4,399.74).
- **Revisions**: the xlsx was replaced on 2025-07-09 to add 2022-2023. The 2008-2017 overlap with the
  PDF archive has not been reconciled.
- **Redistribution**: CDI Privacy Policy, section "Ownership" (https://www.insurance.ca.gov/privacy-policy/, HTTP 200), verbatim:
  "Information and content found on this Website, unless otherwise indicated, is considered in the
  public domain. It may be distributed or copied as permitted by law. In order to use any information
  on this Website not owned or created by CDI you must seek permission directly from the owning (or
  holding) sources." This applies to all CDI entries (S1-S8).

## S2. CDI SB 824 Wildfire Risk Information data (Part II, ZIP-level experience)

- **Landing page**: https://www.insurance.ca.gov/01-consumers/200-wrr/WildfireRiskInfoRpt.cfm (HTTP 200).
  The page is titled "A Fire and Wildfire Exposure Risk Manual - Updated July 7, 2025".
- **Files**:

  | File | URL | Bytes | SHA-256 | Last-Modified |
  |------|-----|-------|---------|---------------|
  | Data xlsx | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Residential-Property-Coverage-Amounts-Wildfire-Risk-and-Losses.xlsx | 7235628 | 1db226b44fbcccae99afff2f6732dae6f656913425d17930df5ff93e7237cf70 | 2025-12-10 |
  | Methodology docx | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Format-of-the-Report-and-Methodology.docx | 26702 | ee879bce1540aec040030be9b70a9c0ec4d35e5718714e728b4cb8e63ade0d6d | 2025-07-09 |

- **Period**: calendar years 2018-2023 (coded `18`-`23`). This is the 2024 report. The methodology says
  2023 losses will be re-evaluated in the 2026 report. The 2026 report was not on the page on the
  retrieval date.
- **Geography**: ZIP code (2,522 ZIPs). Each ZIP is assigned to one county with the USPS City State
  Product. A `z No County` group exists. Only ZIPs with reported data appear. 16 Nevada County ZIPs
  are present, including five that have no 2020 ZCTA (95712, 95724, 95924, 96160, 96162).
- **Policy types**: HO, RT, CO, MH, DO, DT as defined in the methodology. The data also has a seventh
  code, `FP`, that the methodology does not define. Its statewide earned exposure (56,628 in 2018,
  103,290 in 2023) is far below the FAIR Plan policy counts in S7 and S9. Do not treat `FP` as the
  FAIR Plan until CDI confirms the definition. Reporting insurers: admitted insurers with $10M or more
  in residential direct written premium.
- **Units**: earned premium (USD), earned exposure (house-years), average Coverage A and C (USD,
  weighted by the part of the year insured), average PPC class (1-10), average fire risk (0-4 scale),
  counts by fire-risk category (negligible to extreme), claim counts and incurred losses (USD) for
  fire/smoke x Coverage A/C x catastrophe/non-catastrophe.
- **Layout**: one pivot table. The 197,290 source records are in the pivot cache only
  (`xl/pivotCache/pivotCacheRecords1.xml`, 65 MB uncompressed). The `Source` field has three
  mutually exclusive slices: `All Companies`, `Fire Risk Scores` (only insurers that report a risk
  score), `PPC Scores`. Use `All Companies` for premium and exposure. Use `Fire Risk Scores` only
  for risk-category mix.
- **Suppression**: no cell suppression. ZIPs without data are absent, not zero. 15% of `All Companies`
  rows have earned exposure below 5, and 57 rows have negative earned premium.
- **Revisions**: the coverage definition changes in 2021. For 2018-2020, coverage is as of the end of
  the year (or the last day in force). From 2021, it is as of the start of the year (or the first day
  in force). Losses are evaluated at January 31 of the report year, so the latest year is immature
  and is revised in the next report.
- **Redistribution**: CDI terms (see S1).

## S3. CDI new, renewed and nonrenewed policies by ZIP (voluntary market)

- **Landing page**: https://www.insurance.ca.gov/01-consumers/200-wrr/DataAnalysisOnWildfiresAndInsurance.cfm (HTTP 200)
- **Files**:

  | Vintage | URL | Bytes | SHA-256 | Last-Modified |
  |---------|-----|-------|---------|---------------|
  | 2015-2021 | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Residential-Property-Voluntary-Market-New-Renew-NonRenew-by-ZIP-2015-2021.xlsx | 621653 | e225867ebafb56a073e8049afaef2bb0cb45d7d6bc2cb46dabc988ea8e69b667 | 2022-12-21 |
  | 2020-2023 | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Residential-Insurance-Voluntary-Market-New-Renew-NonRenew-by-ZIP-2020-2023.xlsx | 296977 | 316d0ee62f110f9f7f762f052c4e9bb09fb05cd6d9074a796b740247a8f0636d | 2025-01-13 |

- **Period**: 2015-2023. There is no year before 2015 and no year after 2023.
- **Geography**: ZIP with a county label. 2015-2021: 2,654 ZIPs. 2020-2023: 2,171 ZIPs, with 160 rows
  that have a blank county (for example ZIP `90000`).
- **Policy types**: voluntary (admitted) market only: homeowners forms (HO-2, 3, 5, 8 or equivalent),
  dwelling fire (not contents-only), landlord/business-owner residential of 4 units or less, mobile
  homes. Excludes HO-4 and HO-6. FAIR Plan, surplus lines and DIC are not in these files.
- **Units**: policy counts.
- **Layout by vintage**:
  - 2015-2021: one sheet `New Renew NonRenew`. Columns: County, ZIP Code, Year, New, Renewed,
    Insured-Initiated Nonrenewed, Insurer-Initiated Nonrenewed.
  - 2020-2023: one sheet `New Renew Nonrenew`. Columns: County (space-padded), ZIP Code, Year, New,
    Renewed, Non-Renewed. The split between insured- and insurer-initiated nonrenewals is gone.
- **Suppression**: none. Small counts (0-3) are published.
- **Revisions**: 2020 and 2021 appear in both vintages with different values. For example, 95945 in
  2020: new 1,543 / renewed 6,925 (2015-2021 file) vs new 780 / renewed 6,029 (2020-2023 file). The
  county report footnote says: "Starting in 2020, CDI removed certain types of
  non-renewals/cancellations, causing a slight reduction in the number of new, renewed, and
  non-renewed policy counts from prior reports." Treat 2020 as a series break. Use the 2020-2023
  vintage for 2020 onward.
- **Redistribution**: CDI terms (see S1).

## S4. CDI new, renewed and nonrenewed policies by county (voluntary, FAIR Plan, DIC)

- **Landing page**: same as S3.
- **Files**:

  | Vintage | URL | Bytes | SHA-256 | Last-Modified |
  |---------|-----|-------|---------|---------------|
  | 2015-2021 | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Residential-Insurance-Policy-Analysis-by-County-2015-to-2021-2.pdf | 262632 | 85b270f2ec2bee40b7641c55b27eaf664609cdf0bf2b9e9c1488ffd08d20441a | 2023-11-15 |
  | 2020-2023 | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Residential-Insurance-Policy-Analysis-by-County-2020-to-2023-2.pdf | 192004 | c1da5395f4ff8bd5db0eea92674fe354f01617e4f2f52e8f49f657ac88e6dac9 | 2025-01-13 |

- **Period**: 2015-2023.
- **Geography**: county and state. **County level only; this is not ZIP coverage.**
- **Policy types**: voluntary market (same forms as S3), FAIR Plan (new and renewed), and from 2020
  difference-in-conditions (new and renewed).
- **Units**: policy counts.
- **Layout by vintage**: 2015-2021 is 12 PDF pages ("Report Year 2022") with consumer- and
  insurer-initiated nonrenewals split and FAIR Plan new/renewed. 2020-2023 is 5 pages ("Report Year
  2024") with total nonrenewals, FAIR Plan new/renewed and DIC new/renewed. PDF only; tables must be
  parsed from text.
- **Suppression**: none observed.
- **Revisions**: same 2020 break as S3.
- **Redistribution**: CDI terms (see S1).

## S5. CDI fire and earthquake policy counts by county

- **Landing page**: same as S1, section C.
- **Files**:

  | Year | URL | Bytes | SHA-256 | Last-Modified |
  |------|-----|-------|---------|---------------|
  | 2017 | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/C-2017-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf | 72963 | 0c0d204f50b9e98120444ce6229bc5bf909705cbf0297629327fb403fe12417c | 2023-04-20 |
  | 2019 | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/C-2019-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf | 89585 | 12f0ced087b2e080ced666127cc40ff7dcf568e94235beed9579122b9ca3b4de | 2023-04-20 |
  | 2021 | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/C-2021-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf | 89149 | 8bafc2e73bca8f3e1b1136353b631240e9a174d1f0a31c863722f55c4e2223d7 | 2025-06-17 |
  | 2023 | https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/C-2023-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf | 84049 | 0a834e1ede1f866879b4197cb2f397bf8f9dabaeea86ef416cbe2e7456c04082 | 2025-07-09 |

- **Period**: policies in force at 31 December of 2017, 2019, 2021, 2023 (biennial). Even years are not published.
- **Geography**: county and state. Not ZIP.
- **Policy types**: homeowners, dwelling fire (not contents-only), condo, mobile home, renters, each with fire and earthquake counts.
- **Units**: policy counts and percent with earthquake.
- **Layout by vintage**: 2017 is titled "Fire and Earthquake Policy Count per County as of December 31,
  2017" (Statistical Analysis Division) with no footnote. 2019-2023 are titled "The Number of
  Residential Property with Fire or Earthquake Coverage in Calendar Year" (Data Analytics and
  Reporting Division) with a footnote that defines the forms. PDF text extraction splits some numbers
  (for example "4 ,450"), so check parsed totals against the STATEWIDE row.
- **Suppression**: none. Zero cells print as "-".
- **Revisions**: the 2021 file was replaced on 2025-06-17.
- **Redistribution**: CDI terms (see S1).

## S6. CDI share of residential dwelling units insured by the FAIR Plan, by ZIP

- **URL**: https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Number-of-Residential-Dwelling-Units-Insured-in-2022-FAIR-Plan-vs-Voluntary.pdf
- **Bytes / SHA-256**: 508353 / dbc44c37b1e4024eacd06a120e89892a1ea6917b666aed7dcee7afc58dfe0cd8 (Last-Modified 2024-06-18)
- **Period**: calendar year 2022 only.
- **Geography**: ZIP with county and city. 12 Nevada County ZIPs are listed.
- **Policy types**: homeowners (not condo), mobile home, dwelling fire owner- and tenant-occupied, 4
  units or less, from admitted insurers and the FAIR Plan (10 CCR 2646.6 data).
- **Units**: dwelling units insured (voluntary, FAIR Plan) and percent insured by FAIR Plan.
- **Layout**: 43-page PDF table.
- **Suppression**: landing page says "ZIP codes with less than 5 structures insured were excluded."
  95712 and 95924 are absent.
- **Revisions**: single vintage.
- **Redistribution**: CDI terms (see S1).

## S7. CDI fact sheets on residential policies and the FAIR Plan

- **Landing page**: same as S3.
- **Files**:

  | Data year | URL | Bytes | SHA-256 |
  |-----------|-----|-------|---------|
  | 2023 | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/CDI-Fact-Sheet-Summary-on-Residential-Insurance-Policies-and-the-FAIR-Plan-v-011325-2.pdf | 766117 | 1808dbaa3ea6a022e43befe9e97cd5a853632a40fb59f275675a2fccec864c7e |
  | 2022 | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/CDI-Fact-Sheet-Summary-on-Residential-Insurance-Policies-and-the-FAIR-Plan.pdf | 336774 | 2a41b112658ab49540a69ee7080638eb1cb4693ff2ed2039a3378fe67ac618ed |
  | 2021 | https://www.insurance.ca.gov/01-consumers/200-wrr/upload/CDI-Fact-Sheet-Residential-Insurance-Market-Policy-Count-Data-December-2022.pdf | 450305 | 9c5e8900a2fa9b2bc37d0190ad43086732dfa28b81532dd9f67513856df71f6d |
  | 2020 | https://www.insurance.ca.gov/0400-news/0100-press-releases/2021/upload/nr117DataNon-RenewalsandFAIRPlan12202021.pdf | 298870 | 0db7aa40ae2bf797cce836d532290e533102c9f7d39425cef095d295f3902efd |
  | 2019 | https://www.insurance.ca.gov/0400-news/0100-press-releases/2020/upload/nr104Charts-NewRenewedNon-RenewedData-2015-2019-101920.pdf | 794444 | a7114553ca6f6f25ddc02a1cbc7921307cb1ba1d7b098ba5d51bee3ef564e5f2 |
  | 2015-2018 | https://www.insurance.ca.gov/0400-news/0100-press-releases/2019/upload/nr063_factsheetwildfire.pdf | 725850 | f6ed0edeece860035c02e089a05e0357206adb9c7f2e3035f87c1acc03247d2d |

- **Period**: 2015-2023. The latest fact sheet was published 2025-01-13. No 2024 fact sheet was on
  the page on the retrieval date.
- **Geography**: state, plus groups of high-wildfire-risk counties. Not ZIP.
- **Policy types**: voluntary market, FAIR Plan, surplus lines (statewide counts only), DIC. Data
  comes from insurers with $5M or more in written premium, about 98% of the market.
- **Units**: policy counts and shares.
- **Suppression**: none observed.
- **Revisions**: each fact sheet restates earlier years. Use the latest one.
- **Redistribution**: CDI terms (see S1).

## S8. CDI Appendix C, dwelling units in high or very high wildfire risk, by county

- **URL**: https://www.insurance.ca.gov/01-consumers/200-wrr/upload/Availability-and-Affordability-Report-Appendix-C.pdf
- **Bytes / SHA-256**: 75553 / 2cc251d9c6b4a61cb5123bc5756f0d631b10f12f11aed521e97677fea16c43db (Last-Modified 2024-05-31)
- **Period**: one vintage. Dwelling units are from the Department of Finance as of 2015-01-01. The
  date of the modeler risk scores is not stated.
- **Geography**: county. Nevada County: 50,271 dwelling units, 35,282 (70.2%) high/very high.
- **Policy types**: not policy data. Weighted average of several commercial wildfire modelers.
- **Units**: dwelling units, percent.
- **Suppression / revisions**: none stated.
- **Redistribution**: CDI terms (see S1). The modeler scores are third-party, so the CDI "not owned or
  created by CDI" clause may apply.

## S9. California FAIR Plan policies in force and exposure by ZIP and county

- **Landing page**: https://www.cfpnet.com/key-statistics-data/ (HTTP 200). The page says it is
  updated quarterly. On the retrieval date it showed data through June 2026.
- **Files**:

  | File | URL | Bytes | SHA-256 |
  |------|-----|-------|---------|
  | Residential PIF by ZIP | https://www.cfpnet.com/wp-content/uploads/2025/11/CFP-5-yr-PIF-Zip-FY25-DWE-251114.pdf | 608544 | e3db28eb547415b169977a137749f06f7303bab862ff44ab6f3370f08ca5e624 |
  | Residential exposure (TIV) by ZIP | https://www.cfpnet.com/wp-content/uploads/2025/11/CFP-5-yr-TIV-Zip-FY25-DWE-251114.pdf | 666282 | f6fd317921774a289c2951562331325881438cf7d3a186058c5eb73737ad38a7 |
  | All-lines PIF by county | https://www.cfpnet.com/wp-content/uploads/2025/11/CFP-5-yr-PIF-County-FY25-All-251114.pdf | 79366 | 7c3b4286b2acfe21c525ce2fa9ec3348aac5c076b32a4efa48ecaf0ec9feb229 |
  | All-lines TIV by county | https://www.cfpnet.com/wp-content/uploads/2025/11/CFP-5-yr-TIV-County-FY25-All-251114.pdf | 80967 | 4942539f88159b8e79d23e1f8a2a6d8b46625f8e77b85d0619b60f6aed5272a1 |

- **Period**: snapshots at 30 September 2021, 2022, 2023, 2024, 2025 (FAIR Plan fiscal year end).
  These are point-in-time counts, not calendar-year flows.
- **Geography**: ZIP (residential) and county. A ZIP that crosses county lines is assigned to "the
  county the largest population density of that split ZIP Code" (note 2).
- **Policy types**: FAIR Plan dwelling (residential) line. The county files combine residential,
  commercial and business-owner lines.
- **Units**: policies in force (count), total insured value (USD), year-over-year growth (percent).
- **Layout**: PDFs are encrypted with AES. `pypdf` needs the `cryptography` package to read them.
- **Suppression**: none observed. Zero cells print as "-".
- **Revisions**: "Totals by report may vary based on data source." The file names carry the
  generation date (`251114`). Each year the 5-year window moves forward and the earliest year
  drops out. FY2020 and earlier are no longer published (see U3).
- **Redistribution**: FAIR Plan Terms and Conditions (https://www.cfpnet.com/terms-and-conditions/,
  HTTP 200), verbatim: "The content and information on this Website (including, without limitation,
  price and availability of insurance products or services), as well as the infrastructure used to
  provide such content and information (the "System"), is proprietary to us. Accordingly, as a
  condition of using this Website, you agree not to use this Website or its contents or information
  for any purpose (direct or indirect) other than obtaining price quotations and insurance coverage
  for personal or business property." These terms do not grant reuse. Do not commit FAIR Plan files
  or values to the repository until written permission is obtained (see fallbacks).

## S10. California FAIR Plan policies, premium and exposure by category

- **Landing page**: same as S9.
- **Files**:

  | File | URL | Bytes | SHA-256 |
  |------|-----|-------|---------|
  | Residential policy count | https://www.cfpnet.com/wp-content/uploads/2026/07/Policies-by-category-DWE-as-of-260630-DL-260717v001.pdf | 1216694 | e3965c51ff4d84e8fdc8eb060319b108d69098b89ee651a0e6e0af82ba44e452 |
  | Residential premium | https://www.cfpnet.com/wp-content/uploads/2026/07/Premium-by-category-DWE-as-of-260630-DL-260717v001.pdf | 1628275 | 566e240aafe7d0e535cb6c9703c1f35640320f76bace09067752b25fa5490981 |
  | Reporting period changes 2025 | https://www.cfpnet.com/wp-content/uploads/2025/12/Reporting-Period-changes-2025-DL-251023.pdf | 54606 | a8335b18ce1a41164897a77d1dec79024df935903625c89c7d2584a237dae6c9 |

- **Period**: snapshot at 2026-06-30. Earlier quarterly snapshots are replaced, not archived, on the page.
- **Geography**: ZIP with county, region and a distressed-area flag.
- **Policy types**: FAIR Plan residential by occupancy (owner-occupied, tenant-occupied, renters,
  condo unit-owners, other), in three groups of columns.
- **Units**: policy counts; premium (USD); exposure (USD) in the matching exposure file.
- **Suppression / revisions**: none stated. Each quarter overwrites the last.
- **Redistribution**: FAIR Plan terms (see S9).

## S11. Zillow Home Value Index (ZHVI), ZIP

- **URL**: https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv
- **Bytes / SHA-256**: 123559595 / 9dcd2793d97e5f60727bfc10563acdebe8461e48f8af83e09c333512a00521d9 (Last-Modified 2026-09-16)
- **Landing page**: https://www.zillow.com/research/data/ (HTTP 200)
- **Size note**: the file is 124 MB, not ~500 MB. The `fetch_data.py` streaming approach for the
  city file (read rows, keep matching rows) works without change. Filter on `State == "CA"` and
  `CountyName == "Nevada County"`.
- **Period**: monthly, 2000-01 to 2026-08. 95945, 95949, 95959, 95946, 95975, 95977 start 2001-01;
  96161 starts 2000-01. With the model's 10-month rule, full years are 2001-2025 (2000 also for 96161).
  2026 has 8 months.
- **Geography**: ZIP (26,268 ZIPs nationally; 7 in Nevada County). ZIPs without enough sales are absent.
- **Policy types**: not applicable (house prices). All homes (SFR and condo/co-op), 33rd-67th
  percentile tier, smoothed, seasonally adjusted.
- **Units**: USD, nominal.
- **Suppression**: Zillow does not publish ZIPs with too few transactions. Nevada County ZIPs
  with no series: 95712, 95724, 95728, 95924, 95960, 95986, 96111, 96160, 96162.
- **Revisions**: the whole history is recomputed each month. Hash and Last-Modified change monthly.
- **Redistribution**: Zillow Terms of Use (https://www.zillow.com/z/corp/terms/, redirects to
  https://www.zillow.com/corporate/terms-of-use/, HTTP 200), section 4.C, verbatim: "the aggregate
  level data provided on the Zillow Local-Info Pages (the "Aggregate Data") may be used for
  non-personal uses, e.g., real estate market analysis. You may display and distribute derivative
  works of the Aggregate Data (e.g., within a graph), only so long as the Zillow Companies are cited
  as a source on every page where the Aggregate Data are displayed, including "Data Provided by
  Zillow Group." Such citation may not include any of our logos without our prior written approval
  or imply any relationship between you and the Zillow Companies beyond that the Zillow Companies are
  the source of the Aggregate Data."

## S12. Zillow ZHVI, city (already used by `fetch_data.py`)

- **URL**: https://files.zillowstatic.com/research/public_csvs/zhvi/City_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv
- **Bytes / SHA-256**: 93930992 / 9be9ce85c3442fc40d362a009738ae2c8e8ee257c3eadc8d52e4c63a95fb2355 (Last-Modified 2026-09-16)
- **Period**: Grass Valley and Nevada City 2001-01 to 2026-08. Full years 2001-2025.
- **Geography**: city (Zillow region definitions, not Census places).
- Other fields: same as S11.

## S13. Census ACS 5-year estimates, ZCTA

- **Summary file root**: https://www2.census.gov/programs-surveys/acs/summary_file/ (HTTP 200).
  Directories exist for 2005-2025. The 2025 directory `table-based-SF/data/` was empty on the
  retrieval date. The latest 5-year release is 2020-2024.
- **Files fetched** (table B25077, median value of owner-occupied units):

  | Vintage | URL | Bytes | SHA-256 | Last-Modified |
  |---------|-----|-------|---------|---------------|
  | 2020-2024 | https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25077.dat | 18496426 | 89eb2153764a2330b23959d8bbc29b3a8f3016f087b990f2a8ea0d5096d9a816 | 2026-01-29 |
  | 2019-2023 | https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/5YRData/acsdt5y2023-b25077.dat | 18467363 | 302d57367033bd13b824c8d658cd3ef98819adb366d9ddd4c3861a9707eb734c | 2024-10-31 |

  The 2024 file has 33,772 ZCTA rows (`GEO_ID` prefix `860Z200US`). Values for 95945, 95949 and
  95959 are present.
- **Period**: 5-year vintages 2007-2011 through 2020-2024. In the coverage matrix the year is the end
  of the 5-year period. ACS ZCTA data does not exist before the 2007-2011 release. Overlapping
  5-year periods must not be read as annual changes.
- **Geography**: ZCTA. 2011-2019 vintages use 2010 ZCTAs. 2020 onward use 2020 ZCTAs. ZCTAs are
  not ZIPs: 95712, 95724, 95924, 96160, 96162 have no 2020 ZCTA.
- **Units**: per table (USD nominal, counts), with a margin of error column (`_M`).
- **Suppression**: cells can be null or carry annotation values when the sample is too small. Small
  rural ZCTAs (95986, 96111) can have large margins of error.
- **Revisions**: ACS estimates are not revised after release. Geography changes at the 2020 vintage.
- **Access note**: the Census Data API (https://api.census.gov/data.html, HTTP 200) now redirects
  keyless requests to `missing_key.html`. **API use requires a free key**
  (https://www.census.gov/data/developers/api-key.html). The summary-file `.dat` downloads above need
  no key.
- **Redistribution**: Census Bureau works are U.S. Government works and are not under copyright. API
  Terms of Service (https://www.census.gov/data/developers/about/terms-of-service.html, HTTP 200),
  verbatim: "All services, which utilize or access the API, should display the following notice
  prominently within the application: "This product uses the Census Bureau Data API but is not
  endorsed or certified by the Census Bureau."" This applies to S14 and S17 as well.

## S14. Census ZCTA relationship files

- **Files**:

  | Vintage | URL | Bytes | SHA-256 |
  |---------|-----|-------|---------|
  | 2020 ZCTA to 2020 county | https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt | 6821287 | 3ed41278d637dc249e0323306f68be8a6c234e3090f4de88ef328dee71aeaaaf |
  | 2010 ZCTA to 2020 ZCTA | https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta510_zcta520_natl.txt | 18310430 | 800643129ab0f4010f84401c36c24099c01ccf5c778a9f6e1f974655029022fb |
  | 2010 ZCTA to 2010 county | https://www2.census.gov/geo/docs/maps-data/data/rel/zcta_county_rel_10.txt | 6574059 | ea4798cfffbed5eaba990842ca0b1984e97d16ed7d3e0b4ef3e2d9216d5b8980 |

- **Period**: fixed vintages 2010 and 2020. Not annual.
- **Geography**: ZCTA x county (area and land-area shares; 2010 file also has population and housing
  unit shares). 12 ZCTAs touch Nevada County in 2020 and 13 in 2010 (95715 only in 2010). Both
  include 95602 (mostly Placer) and 96111 (border). 95724 had a 2010 ZCTA (in Placer only) and has
  no 2020 ZCTA.
- **Units**: square meters, counts, percentages.
- **Suppression / revisions**: none.
- **Redistribution**: Census (see S13).

## S15. HUD USPS ZIP crosswalk (proposed)

- **URL**: https://www.huduser.gov/portal/datasets/usps_crosswalk.html. **Requires login/request**:
  the scripted request got HTTP 202 with an empty body (bot challenge) on two tries. The download
  app (https://www.huduser.gov/apps/public/uspscrosswalk/home) also returned HTTP 202. File downloads
  and the API (https://www.huduser.gov/portal/dataset/uspszip-api.html) need a free HUD User account
  and an API token.
- **Why it matters**: it is the only public source that maps PO-box and unique ZIPs (95712, 95724,
  95924, 96160, 96162) to tracts, counties and ZCTAs, with residential address ratios.
- **Period / geography / units / suppression / revisions**: not verified, because no file was
  retrieved. HUD describes it as quarterly.
- **Redistribution**: not verified. Record the terms when an account is created.

## S16. FRED series (already used by `fetch_data.py`)

Retrieved with `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>` (HTTP 200).

| ID | Source agency | Geography | Units | First obs | Last obs | Bytes | SHA-256 | FRED copyright label |
|----|---------------|-----------|-------|-----------|----------|-------|---------|----------------------|
| MORTGAGE30US | Freddie Mac PMMS | national | percent, weekly | 1971-04-02 | 2026-10-01 | 46947 | 3ffacebbee5e0acd5f2f7b7effddb2bfbc54b89dd85405475e37545cd3be98db | Copyrighted: Citation Required |
| CPIAUCSL | BLS | national | index 1982-84=100, monthly SA | 1947-01 | 2026-08 | 17744 | f8ecddf53a9a9a74dda92c2c4204e6039466fcb744bc74171b73b5f6aac48119 | Public Domain: Citation Requested |
| ATNHPIUS06057A | FHFA | county (Nevada) | index 2000=100, annual | 1976 | 2025 | 908 | 54d630b1a9b9c55a87d58d4e9c7573c33956231af8bfa5976ff6c96241bae116 | Public Domain: Citation Requested |
| PCPI06057 | BEA | county (Nevada) | USD, annual | 1969 | 2024 | 967 | 6039106de9c0dbeeb64b6237dea4574b4e5e76f44c5ee156047e7fd226c563c4 | Public Domain: Citation Requested |
| BPPRIV006057 | Census | county (Nevada) | housing units, annual | 1990 | 2025 | 569 | 837552aaf6e37fc73814861a82875830aa0ffb31d40d4c61227030ee6be864f4 | Public Domain: Citation Requested |

- **Period**: 2026 is partial for the weekly and monthly series. October 2025 CPI was not
  published (see `fetch_data.py`), so 2025 is an 11-month mean.
- **Policy types**: not applicable.
- **Suppression**: some small counties have no FRED county series (`fetch_data.py` handles the
  empty response).
- **Revisions**: FHFA notes that "these annual county indexes should be considered developmental"
  and are revised. BEA revises county income each year. Freddie Mac changed the PMMS method on
  2022-11-17.
- **Redistribution**: FRED legal page (https://fred.stlouisfed.org/legal/, HTTP 200), verbatim for
  "Copyrighted: Citation required": "These series are under copyright; but, provided you have not
  engaged in any prohibited uses, you may use these data series with proper attribution of the source
  and acknowledgment that you obtained the data from FRED (example, " Source: BLS via FRED ") when
  displaying or publishing it." The MORTGAGE30US series page says "Copyright, 2016, Freddie Mac.
  Reprinted with permission."

## S17. Census population estimates (already used by `fetch_data.py`)

| Vintage | URL | Bytes | SHA-256 | Years |
|---------|-----|-------|---------|-------|
| 2000-2010 intercensal | https://www2.census.gov/programs-surveys/popest/datasets/2000-2010/intercensal/county/co-est00int-tot.csv | 370855 | 273e56203b61c2ccfb05a54c9b2b025b0521e3764b175469ca22bcad4b6ae8f6 | 2000-2010 |
| Vintage 2020 | https://www2.census.gov/programs-surveys/popest/datasets/2010-2020/counties/totals/co-est2020-alldata.csv | 4012355 | 983708e1b68c8e7c4ecbef4d45137bea34ee286abe07662ccc0d6dfc6411fe83 | 2010-2020 |
| Vintage 2025 | https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/totals/co-est2025-alldata.csv | 2071735 | 4f5a499d851e2cb48fd7a5405e5a9235453a8a66933657aacd10df0e264f35d5 | 2020-2025 |

- **Geography**: county. **Units**: persons, July 1.
- **Revisions**: each vintage revises all post-census years. No 2010-2020 intercensal county file
  exists (`fetch_data.py` smooths the seam).
- **Redistribution**: Census (see S13).

## Unavailable sources

- **U1. ZIP-level admitted homeowners premium before 2018.** Part I (S1) is statewide only. The
  SB 824 data (S2) starts in 2018. No CDI ZIP premium series for 2000-2017 was found on the
  homeowners study page or the wildfire data page.
- **U2. CDI ZIP-level data for 2024 or later.** On 2026-10-05 the latest years were 2023 for S2, S3,
  S4 and S7. The SB 824 methodology refers to a 2026 report. It was not published on the retrieval date.
- **U3. FAIR Plan ZIP policy counts before FY2021.** The FAIR Plan page publishes a rolling 5-year
  window. Earlier windows are not linked. Before FY2021, the only FAIR Plan geography is county
  (S4, from 2015) or ZIP for 2022 only (S6).
- **U4. Surplus lines policies below state level.** Only statewide counts appear in the fact
  sheets (S7).

## Pre-shock coverage

For trend tests, the pre-shock window is the years before the 2017-2018 wildfire seasons and the
2019 nonrenewal spike. Verified coverage of that window for Nevada County:

- House prices: ZIP ZHVI 2001-2017, city ZHVI 2001-2017, FHFA county HPI 2000-2017.
- Insurance quantities: ZIP voluntary new/renewed/nonrenewed 2015-2018 (S3); county FAIR Plan
  2015-2018 (S4); county policy counts 2017 (S5).
- Insurance prices: statewide average premium 2001-2017 (S1) only. **No ZIP or county premium
  exists before 2018.**

## Fallbacks and stop conditions

**Missing ZIP-level premium (2000-2017).**
1. Do not backcast ZIP premium as if it were observed. If a pre-2018 premium level is needed, scale
   the statewide homeowners average written premium (S1) by each ZIP's 2018 ratio to the state
   (from S2, `All Companies`, HO). Label the result "modeled" in outputs and keep it in a separate
   column.
2. Use the voluntary-market nonrenewal rate (S3, 2015 onward) and the county FAIR Plan share (S4,
   2015 onward) as the pre-shock insurance-availability measures. They are counts, not prices.
3. Stop: if an analysis needs observed ZIP premium before 2018 to identify its effect, do not run it.
   Reduce the question to 2018-2023, or request historical ZIP data from CDI
   (ClimateStudies@insurance.ca.gov).

**Missing ZIP-level insurance data (2024 onward).**
1. Do not carry 2023 values forward. Report insurance series through 2023 and end the series there.
2. For 2024-2025, the only ZIP insurance signal is FAIR Plan policies in force and exposure at
   30 September (S9). Show it as a separate FAIR Plan series. Do not merge it into the CDI series.
3. Stop: rerun this audit when CDI publishes the 2026 SB 824 report or a 2024 nonrenewal file. Update
   `docs/coverage_matrix.csv` before any model uses 2024 insurance data.

**County-only years.** S4, S5 and S8 are county series. Use them only as county controls or to
check ZIP sums. Never write them into a ZIP column or mark them as ZIP coverage.

**Series breaks.**
- S3/S4 2020 definitions changed: use the 2020-2023 vintage for 2020 onward and add a break
  indicator at 2020. Do not splice levels across the break without an overlap adjustment from
  2020-2021.
- S2 coverage amounts change from end-of-year to start-of-year in 2021: compare Coverage A levels
  only within 2018-2020 or within 2021-2023.
- S13 ZCTA geography changes at the 2020 vintage: crosswalk 2010 ZCTAs to 2020 ZCTAs with S14.
- S2 policy type `FP` is undefined: exclude it until CDI defines it.

**ZIP to ZCTA and county.** Join ZIP data to ACS with S14 for ZIPs that have a ZCTA. ZIPs with
no 2020 ZCTA (95712, 95724, 95924, 96160, 96162) need the HUD crosswalk (S15). Until a HUD account exists,
drop them from ZCTA joins and list them as dropped. Four are small (HO earned exposure at most
30 house-years in any SB 824 year). 95724 (Norden) is larger (up to 157) and needs the crosswalk
before any Truckee-area analysis.

**Reuse rights.**
- CDI, Census, BLS, BEA, FHFA data: may be committed to `data/` and shown on the site with a citation.
- Zillow: may be shown as derived graphs with "Data Provided by Zillow Group" on every page that
  shows it.
- MORTGAGE30US: cite "Freddie Mac via FRED".
- FAIR Plan (S9, S10): the site terms give no reuse right. Stop: do not commit FAIR Plan files or
  publish FAIR Plan values until the FAIR Plan grants written permission. Until then, use the
  CDI-published FAIR Plan counts (S4, S6, S7), which are under CDI terms.
