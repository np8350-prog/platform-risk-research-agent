# Go-to-Market: Future Sprints

## Positioning

A self-serve AI vendor risk diagnostic, built for one buyer making one
decision, not a security team managing a large vendor portfolio. The
scoring is grounded in an authored risk framework, not a generic
compliance checklist, that's the differentiator against existing
enterprise TPRM tools (Vanta, Prevalent, VISO TRUST, Torii).

## Target Buyer

A company (fintech-scoped initially) deciding whether to adopt an AI
vendor or no-code platform, who wants a structured, evidence-graded
answer in minutes instead of a slow manual security review or a sales
call.


## Sprint 1: Ship Free

**Goal:** audience and inbound leads, not revenue.

- Ship the diagnostic free, self-serve.
- No account required to run a single diagnostic.
- Build awareness through the same channels the broader Groundwork
  platform already targets.

**Success metric:** usage volume and repeat visits, not conversion.


## Sprint 2: Paid Deep-Report Tier

**Goal:** first revenue, tied to consulting work already happening.

- A paid tier for a deeper report: more alternatives compared, longer
  evidence trail, exportable formats beyond PDF.
- Alternatively, offered as a "run this before you sign the vendor
  contract" add-on for existing consulting clients, positioned as due
  diligence insurance, not a separate product purchase.

**Success metric:** conversion rate from free diagnostic to paid deep
report; add-on attach rate on consulting engagements.


## Sprint 3: Partnership Motion

**Goal:** distribution beyond direct traffic.

- Offered as a pre-screen tool through fintech vendor directories or AI
  tool marketplaces, where a buyer is already comparing options and a
  trust score is a natural next step.
- Explore integration into the marketplace's own listing pages, so a
  score can be requested without leaving the directory.

**Success metric:** number of active partnership integrations; referral
volume from partner surfaces.


## Longer-Term: Groundwork Integration

The report schema (`PlatformRiskReport`) was deliberately modeled on
Groundwork's own diagnostic report shape from the start specifically so
this agent could become a live diagnostic card inside Groundwork later
with no rework to the output format. See `docs/ARCHITECTURE.md` for the
schema details.
