# LEGAL REFERENCES - for orientation only

Supplied by the project team (reviewer), 2026-11-26. The tool reads them from `config/legal.yaml`, which holds this table and nothing more.
These references tell a reader where a concept of the tool sits in EU law. They are not legal advice and not a
statement of any authority's duties. The tool models none of the legal conditions: it only labels its concepts.

Acts:
- **BRRD**: Directive 2014/59/EU, as amended by Directive (EU) 2026/806 (OJ L, 20.4.2026; to be transposed by 11 May 2028, measures apply from 12 May 2028).
- **DGSD**: Directive 2014/49/EU, as amended by Directive (EU) 2026/804.
- **Handbook**: EBA/REP/2025/20.

Status column: **A** = read by the reviewer in the Official Journal text of Directive (EU) 2026/806 on 2026-11-26.
**B** = article of the directive as known to the reviewer, not re-read on that date: to be confirmed by a practitioner before the references are shown as final.

| Concept in the tool | Reference | Status |
|---|---|---|
| The exercise itself: Union-wide exercises coordinated by the EBA, on the application of the BRRD, the SRMR and the DGSD in cross-border situations; EBA publishes a report of key findings | BRRD Article 128c "Crisis management simulations" | A |
| Early intervention measures (competent authority) | BRRD Article 27 "Early intervention measures" | A |
| Competent authority informs the resolution authority; preparation for resolution | BRRD Article 30a "Preparation for resolution" | A |
| Conditions for resolution; the institution is failing or likely to fail | BRRD Article 32(1), point (a) | A |
| When an institution is deemed failing or likely to fail | BRRD Article 32(4) | B |
| Public interest assessment ("no public interest, liquidation") | BRRD Article 32(5) | A |
| Moratorium (power to suspend certain obligations) | BRRD Article 33a | A |
| Valuation before resolution action; provisional valuation | BRRD Article 36 (provisional valuation: paragraph 9) | A for the article, B for the paragraph |
| Resolution tools, general | BRRD Article 37 | B |
| Sale of business tool | BRRD Articles 38 and 39 | B |
| Bail-in tool | BRRD Articles 43 and 44 | B |
| Contribution of at least 8 % of total liabilities including own funds before the resolution financing arrangement contributes (NOT modelled: see LIMITS) | BRRD Article 44(5), point (a) | A |
| MREL | BRRD Articles 45 to 45m | B |
| Order of write-down and conversion (the "liability stack") | BRRD Article 48 | B |
| Write-down and conversion of capital instruments | BRRD Articles 59 and 60 | B |
| No creditor worse off than in insolvency | BRRD Article 34(1), point (g), and Articles 73 to 75 | B |
| Resolution colleges (home and host authorities) | BRRD Article 88 | A |
| Ranking of deposits in insolvency | BRRD Article 108 | A |
| Use of deposit guarantee schemes in resolution | BRRD Article 109 | A |
| Coverage level of EUR 100 000 | DGSD Article 6 | B |
| Repayment of covered deposits within seven working days | DGSD Article 8 | B |
| Use of DGS funds | DGSD Article 11 | B |
| Emergency liquidity assistance | Not in the BRRD or DGSD: a central bank matter (national central banks; in the euro area under the Eurosystem's ELA agreement). Give no article. | - |

## How to use them in the tool
1. One file `config/legal.yaml` holding this table (concept key, reference, status). No reference is written anywhere else.
2. Where a decision, a stage or a resolution figure appears in an inject, a control-team document, the dossier's liability stack, the closing report and the app's decision cards: a small "Reference:" line, from the file. Status B references carry "(to be confirmed)".
3. A switch "show legal references" (on as shipped) in the export and in the app.
4. A page of the control team pack and a tab under "Assumptions and limits" list the whole table with its status and the sentence: "References are for orientation only. The tool does not model the legal conditions."
5. METHODOLOGY and LIMITS: one paragraph each. The home page and METHODOLOGY cite Article 128c, with the words "in cross-border situations", only as context: why such exercises exist.
6. Tests: every reference shown comes from the file; no article number appears in any template outside it; with the switch off no reference appears.
7. Do not add, extend or rephrase any reference beyond this table. If a concept of the tool has no row, it gets no reference.
