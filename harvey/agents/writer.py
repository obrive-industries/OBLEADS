"""Writer — crafts personalized email sequences.

With a native mail provider (Gmail/SMTP), email 1 is written PER PROSPECT
from grounded facts — the company's own description, detected tech stack,
and buying signals — instead of one merge-tag template shared by a whole
batch. Mass-templated "token-swap" mail is exactly what inbox filters now
cluster and junk; a specific, verifiable first line is what earns replies.
Steps 2-3 stay campaign-level (proof point + breakup are less personal by
design). Every draft still passes the deterministic pre-send gate.
"""

import json
import logging
from datetime import datetime, timezone

from harvey.brain import Brain
from harvey.config import HarveyConfig
from harvey.integrations.mail_provider import NATIVE_PROVIDERS
from harvey.models.campaign import Campaign, EmailStep
from harvey.state import StateManager

logger = logging.getLogger("harvey.writer")

# Native mode: one Claude call per prospect for email 1, so batches stay
# small — the rest of the 'new' pool is picked up on later cycles.
NATIVE_BATCH_CAP = 8
LEGACY_BATCH_CAP = 50


class Writer:
    def __init__(
        self,
        brain: Brain,
        state: StateManager,
        config: HarveyConfig,
        env=None,
    ):
        self.brain = brain
        self.state = state
        self.config = config
        self.env = env

    @property
    def is_native(self) -> bool:
        return self.config.channels.email.provider in NATIVE_PROVIDERS

    async def run(self):
        """Create email campaigns for prospects that need outreach."""
        logger.info("Writer: Crafting email campaigns...")

        # Load foundational skills for this agent
        self.skills = self.brain.load_skills_for_agent("writer")

        # Get prospects that haven't been contacted yet
        new_prospects = await self.state.get_prospects_by_status("new")
        if not new_prospects:
            logger.info("Writer: No new prospects to write for.")
            return

        # Only write for prospects we can actually deliver to. Guessed and
        # invalid addresses are skipped so we don't spend Claude calls (or
        # sending reputation) on mail that will bounce.
        deliverable = {"verified", "risky"}
        prospects_with_email = [
            p for p in new_prospects
            if p.email and (p.email_status or "guess") in deliverable
        ]
        if not prospects_with_email:
            logger.info(
                "Writer: No prospects with deliverable (verified/risky) emails yet."
            )
            return

        # Batch prospects into campaign groups (by industry/title for relevance)
        batches = self._group_prospects(prospects_with_email)

        for batch_name, prospects in batches.items():
            if not prospects:
                continue

            logger.info(
                f"Writer: Creating campaign '{batch_name}' for "
                f"{len(prospects)} prospects."
            )

            # Generate the email sequence
            sequence = await self._write_sequence(prospects)
            if not sequence:
                logger.warning(f"Writer: Failed to generate sequence for {batch_name}")
                continue

            # Create the campaign
            campaign = Campaign(
                id="",
                name=batch_name,
                channel="email",
                sequence=sequence,
                prospect_ids=[p.id for p in prospects],
                status="draft",
            )
            campaign_id = await self.state.add_campaign(campaign)

            # Mark prospects so they aren't picked up again
            for p in prospects:
                await self.state.update_prospect_status(p.id, "queued")

            # Native providers: replace the shared template for email 1 with
            # a per-prospect draft grounded in that company's actual facts.
            if self.is_native:
                await self._personalize_first_emails(campaign_id, prospects)

            await self.state.log_action(
                action_type="write_campaign",
                agent="writer",
                details={
                    "campaign_id": campaign_id,
                    "campaign_name": batch_name,
                    "prospect_count": len(prospects),
                    "steps": len(sequence),
                },
            )
            logger.info(
                f"Writer: Campaign '{batch_name}' created with "
                f"{len(sequence)} emails for {len(prospects)} prospects."
            )

    async def _personalize_first_emails(self, campaign_id: str, prospects: list):
        """Draft a grounded, per-prospect email 1 and stage it in the outbox.

        The outbox unique index means the sender's later template staging
        can't overwrite these rows — a personalized draft always wins the
        (campaign, prospect, step 1) slot. If a draft fails, the slot stays
        empty and the sender falls back to the campaign template.
        """
        require_approval = getattr(
            self.config.channels.email, "require_approval", True
        )
        status = "pending_review" if require_approval else "approved"
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        provider = self.config.channels.email.provider

        drafted = 0
        for prospect in prospects:
            try:
                draft = await self._write_personal_email(prospect)
            except Exception as e:
                logger.warning(f"Writer: personal draft failed for {prospect.email}: {e}")
                continue
            if not draft:
                continue
            item_id = await self.state.add_outbox_item(
                prospect_id=prospect.id,
                campaign_id=campaign_id,
                step=1,
                to_email=prospect.email,
                subject=draft["subject"],
                body=draft["body"],
                send_at=now,
                status=status,
                provider=provider,
            )
            if item_id:
                drafted += 1

        if drafted:
            logger.info(
                f"Writer: drafted {drafted} personalized first email(s) "
                f"({'awaiting approval' if require_approval else 'approved'})."
            )

    async def _write_personal_email(self, prospect) -> dict | None:
        """One grounded draft for one person. Facts in, one email out."""
        facts = [
            f"- Name: {prospect.full_name()}",
            f"- Title: {prospect.title}",
            f"- Company: {prospect.company}",
        ]
        if prospect.industry:
            facts.append(f"- Industry: {prospect.industry}")
        if prospect.personalization_notes:
            facts.append(f"- Research notes: {prospect.personalization_notes}")

        sender_name = getattr(self.config.persona, "name", "Dr. Malakh Jibrail") or "Dr. Malakh Jibrail"
        sender_role = getattr(self.config.persona, "role", "Chief Executive Officer") or "Chief Executive Officer"

        prompt = f"""You are {sender_name}, {sender_role} at Obrive Industries (https://obrive.com).
Tone: visionary, authoritative, consultative, professional.

Obrive Enterprise Portfolio:
- Website: https://obrive.com | https://obrive.in
- Brands: https://obnov.in | https://obzor.in
- Services: https://obrive.com/services (16 core services: AR/VR/MR development, WebXR 3D product configurators, Digital Twins, custom LLMs, AI consulting)
- Core Capabilities: Cutting-edge Augmented Reality (AR), Virtual Reality (VR), Mixed Reality (MR), Spatial Computing & 3D WebXR.
- Proprietary Products: OBNEST (AR/VR/WebXR 3D virtual real estate tours & digital twins), OBMOVE (AR/VR 3D vehicle configurators & virtual showrooms), OBNAVI (spatial wayfinding), OBPARK (smart parking).
- Booking: https://calendly.com/obrive-inc/talk-to-ob-experts?country=us

Write ONE cold business development email to this specific person:
{chr(10).join(facts)}

Requirements:
- Length: 120-170 words.
- Clearly introduce {sender_name} ({sender_role}) from Obrive Industries.
- Tailor why we are reaching out to their specific company ({prospect.company}) and industry.
- Detail the relevant Obrive solution (for Real Estate: OBNEST WebXR interactive 3D virtual walkthroughs & digital twins; for Automotive: OBMOVE 3D vehicle configurators & virtual showrooms; both powered by AR/VR/MR and Spatial Computing).
- State that zero app download is required (runs directly in mobile/desktop web browsers) and achieves up to 94% conversion lift.
- Invite them to explore our services at https://obrive.com/services and book a 15-minute live demo at https://calendly.com/obrive-inc/talk-to-ob-experts?country=us.
- Include full professional signature:
  Warm Regards,
  {sender_name}
  {sender_role}
  Obrive Industries
  Sree Gururaya Mansion, 3rd Floor, No 759, 8th Main Road
  KSRTC Layout, 3rd Phase, JP Nagar, Bengaluru, Karnataka – 560078
  Connect Me : +91 988-6944-447
  Connect Us : +91 888-4774-300
  Visit Us at : www.obrive.com / www.obrive.in
  Our Brands : www.obnov.in / www.obzor.in
- Subject: lowercase, 3-6 words, professional.

Return ONLY JSON: {{"subject": "...", "body": "..."}}"""

        result = await self.brain.think_json(
            prompt, session_id="harvey-writer",
            agent="writer", task="personal_email",
        )
        if not isinstance(result, dict):
            return None
        subject = str(result.get("subject") or "").strip()
        body = str(result.get("body") or "").strip()
        if not subject or not body:
            return None
        return {"subject": subject[:120], "body": body[:2000]}

    async def _write_sequence(self, prospects: list) -> list[EmailStep]:
        """Ask the brain to write a 3-email sequence."""
        prospect_summary = "\n".join(
            f"- {p.full_name()}, {p.title} at {p.company}"
            + (f" | Notes: {p.personalization_notes}" if p.personalization_notes else "")
            for p in prospects[:5]
        )

        industry = prospects[0].industry if prospects else "Enterprise"
        sender_name = getattr(self.config.persona, "name", "Dr. Malakh Jibrail") or "Dr. Malakh Jibrail"
        sender_role = getattr(self.config.persona, "role", "Chief Executive Officer") or "Chief Executive Officer"

        prompt = f"""You are {sender_name}, {sender_role} at Obrive Industries (https://obrive.com).
Tone: visionary, authoritative, consultative, professional.

Obrive Enterprise Portfolio:
- Website: https://obrive.com | https://obrive.in
- Brands: https://obnov.in | https://obzor.in
- Services: https://obrive.com/services (16 core services: AR/VR/MR development, WebXR 3D product configurators, Digital Twins, custom LLMs, AI consulting)
- Core Capabilities: Cutting-edge Augmented Reality (AR), Virtual Reality (VR), Mixed Reality (MR), Spatial Computing & 3D WebXR.
- Proprietary Products: OBNEST (AR/VR/WebXR 3D virtual real estate tours & digital twins), OBMOVE (AR/VR 3D vehicle configurators & virtual showrooms), OBNAVI (spatial wayfinding), OBPARK (smart parking).
- Booking: https://calendly.com/obrive-inc/talk-to-ob-experts?country=us

Write a 3-email cold business development sequence for {industry} prospects like these:
{prospect_summary}

Requirements:
- Email 1 (120-170 words):
  * Introduce {sender_name} ({sender_role}) from Obrive Industries.
  * State why we are reaching out, tailored specifically to {{{{company}}}} and their projects in {industry}.
  * Detail our solution (for Real Estate: OBNEST WebXR interactive 3D virtual walkthroughs & digital twins; for Automotive: OBMOVE 3D vehicle configurators & virtual showrooms; leveraging AR/VR/MR and spatial computing).
  * Highlight advantage: zero app install needed (runs smoothly in standard mobile & desktop web browsers) and delivers up to 94% conversion lift.
  * Invite to explore https://obrive.com/services and book a 15-min interactive demo at https://calendly.com/obrive-inc/talk-to-ob-experts?country=us.
  * Signature:
    Warm Regards,
    {sender_name}
    {sender_role}
    Obrive Industries
    Sree Gururaya Mansion, 3rd Floor, No 759, 8th Main Road
    KSRTC Layout, 3rd Phase, JP Nagar, Bengaluru, Karnataka – 560078
    Connect Me : +91 988-6944-447
    Connect Us : +91 888-4774-300
    Visit Us at : www.obrive.com / www.obrive.in
    Our Brands : www.obnov.in / www.obzor.in

- Email 2 (Follow-up, 80-120 words):
  * Follow up on the previous note with a brief case-study metric (e.g. 60% faster visualization cycle, 40% reduction in return/indecision rates).
  * Reiterate the link to test live demos or schedule a chat.
  * Include signature.

- Email 3 (Closing, 40-70 words):
  * Courteous check-in to see if AR/VR/MR, spatial computing or 3D digital twins are on their roadmap.
  * Leave the door open with links to Obrive and the demo calendar.
  * Include signature.

- Use {{{{first_name}}}}, {{{{company}}}}, {{{{title}}}} as merge variables.
- Subject lines: lowercase, 3-6 words, professional.

Return ONLY a valid JSON array:
[
  {{"step": 1, "subject": "...", "body": "...", "delay_days": 0}},
  {{"step": 2, "subject": "...", "body": "...", "delay_days": 3}},
  {{"step": 3, "subject": "...", "body": "...", "delay_days": 4}}
]"""

        result = await self.brain.think_json(
            prompt, session_id="harvey-writer",
            agent="writer", task="write_sequence",
        )
        return self._parse_sequence(result)

    def _parse_sequence(self, result) -> list[EmailStep]:
        """Robustly coerce LLM output into a validated EmailStep list.

        Never raises. Tolerates a wrapper dict ({"emails": [...]}), missing
        or wrong-typed fields, and extra keys. Drops invalid steps rather
        than failing the whole sequence.
        """
        if isinstance(result, str):
            result = self.brain._extract_json(result)
        if isinstance(result, dict):
            # Model wrapped the array in an object — unwrap common keys.
            for key in ("emails", "sequence", "steps", "campaign", "data"):
                if isinstance(result.get(key), list):
                    result = result[key]
                    break
            else:
                # If the dict itself is a single step
                if "subject" in result and "body" in result:
                    result = [result]
        if not isinstance(result, list) or not result:
            logger.error(f"Writer: Brain did not return an email array (got {type(result).__name__}).")
            return []

        steps: list[EmailStep] = []
        for i, raw in enumerate(result[:5]):  # never accept absurdly long sequences
            if not isinstance(raw, dict):
                logger.warning(f"Writer: Skipping non-dict step at index {i}.")
                continue
            subject = str(raw.get("subject") or "").strip()
            body = str(raw.get("body") or "").strip()
            if not subject or not body:
                logger.warning(f"Writer: Skipping step {i + 1} with empty subject/body.")
                continue
            try:
                delay = max(0, int(raw.get("delay_days", 3 if steps else 0)))
            except (TypeError, ValueError):
                delay = 3 if steps else 0
            try:
                steps.append(
                    EmailStep(
                        step=len(steps) + 1,
                        subject=subject,
                        body=body,
                        delay_days=delay,
                    )
                )
            except Exception as e:
                logger.warning(f"Writer: Skipping invalid step {i + 1}: {e}")

        if steps and steps[0].delay_days != 0:
            steps[0].delay_days = 0  # first email always sends immediately

        if len(steps) < 3:
            logger.warning(f"Writer: Expected 3 emails, got {len(steps)}.")
        return steps

    def _group_prospects(self, prospects: list) -> dict[str, list]:
        """Group prospects into campaign batches by industry/title combo."""
        batches: dict[str, list] = {}

        for prospect in prospects:
            # Group by industry + rough title category
            industry = prospect.industry or "general"
            key = f"{industry}-outreach"

            if key not in batches:
                batches[key] = []
            batches[key].append(prospect)

        # Cap batch size. Native mode stays small because email 1 is drafted
        # per prospect (one Claude call each); leftovers ride the next cycle.
        # Small segments also outperform blasts on deliverability.
        cap = NATIVE_BATCH_CAP if self.is_native else LEGACY_BATCH_CAP
        return {key: group[:cap] for key, group in batches.items()}
