"""
Step 5: build the golden question set (eval/golden.jsonl) from ATT&CK's own structure.

Every question comes from a template over real objects, so the correct answers come from the data, not from
someone's judgment. Relevance is graded:
  2 = the primary answer (the page that answers the question)
  1 = also relevant (e.g. the parent technique, one piece of software in a group's list)

Template questions are stiff ("What is Scheduled Task?"), and copying a document's title into the question makes
keyword search look better than it is. So Claude Haiku 5.5 rewrites each one the way an analyst would ask it
(for technique questions: without reusing the exact title), then translates it to Arabic, keeping tool names,
group names and IDs in Latin script as Arabic-speaking analysts write them. ID questions need no rewriting.

Deterministic: fixed random seed; LLM outputs are cached in eval/golden_llm_cache.json so re-running the script
reproduces the same file without new API calls.

Run:  python -m products.attack.golden
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import defaultdict

from core.llm import Claude
from core.schema import Document
from products.attack.ingest import DOCS_PATH, REL_PATH, ROOT

OUT_PATH = ROOT / "eval" / "golden.jsonl"
CACHE_PATH = ROOT / "eval" / "golden_llm_cache.json"
SEED = 7
COUNTS = {"definition": 10, "detect": 10, "mitigate": 8, "group_software": 6, "software_groups": 6,
          "alias": 6, "campaign": 4, "id": 8, "revoked_id": 6, "bare_id": 4}

PARAPHRASE = (
    "Rewrite the question below the way a SOC analyst would type it into a search box: natural, specific, one "
    "line. {rule} Use the description only to understand the topic; do not copy its wording. Never add an "
    "ATT&CK ID, a threat group name, a malware or tool name, or any other part of the answer that is not already "
    "in the question: the question is used to test a search engine, so it must not contain its own answer. "
    "Reply with the question only.\n\nQuestion: {question}\nDescription: {description}{retry}")
RETRY = "\n\nYour previous rewrite contained \"{leak}\", which gives away the answer. Leave it out."
RULE_DESCRIBE = "Do not use the exact name \"{title}\"; describe the behaviour instead."
RULE_KEEP = "Keep the name \"{title}\"."
TRANSLATE = (
    "Translate this question into Modern Standard Arabic, the way an Arabic-speaking SOC analyst would write it. "
    "Keep tool names, group names, product names and ATT&CK IDs in Latin script exactly as written. Reply with the "
    "Arabic question only.\n\n{question}")


class LLMCache:
    def __init__(self):
        self.data = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
        self.llm = None

    def ask(self, prompt: str) -> str:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:20]  # the prompt itself is not kept in the repo
        if key not in self.data:
            self.llm = self.llm or Claude("claude-haiku-5-5", purpose="golden set")
            self.data[key] = self.llm.complete(prompt, max_tokens=200).strip().strip('"')
            CACHE_PATH.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.data[key]


def build() -> list[dict]:
    with DOCS_PATH.open(encoding="utf-8") as f:
        docs = {d.doc_id: d for d in map(Document.model_validate_json, f)}
    with REL_PATH.open(encoding="utf-8") as f:
        rels = [json.loads(line) for line in f]
    out, into = defaultdict(list), defaultdict(list)
    for r in rels:
        out[(r["source"], r["relation"])].append(r["target"])
        into[(r["target"], r["relation"])].append(r["source"])
    rng = random.Random(SEED)
    of_type = lambda t: sorted(d for d, doc in docs.items() if doc.type == t)  # noqa: E731
    techniques = of_type("technique")
    pick = lambda pool, n: rng.sample(sorted(pool), min(n, len(pool)))  # noqa: E731

    def family(t: str) -> dict[str, int]:
        """A technique's parent and sub-techniques count as also-relevant."""
        grades = {p: 1 for p in out[(t, "subtechnique-of")]}
        grades |= {c: 1 for c in into[(t, "subtechnique-of")]}
        return grades

    intents = []  # (template, english template question, relevance, title, rule)
    for t in pick(techniques, COUNTS["definition"]):
        intents.append(("definition", f"What is the ATT&CK technique \"{docs[t].title}\"?", {**family(t), t: 2}, t, "describe"))
    with_det = [t for t in techniques if into[(t, "detects")]]
    for t in pick(with_det, COUNTS["detect"]):
        rel = {t: 1, **{d: 2 for d in into[(t, "detects")]}}
        intents.append(("detect", f"How can I detect \"{docs[t].title}\"?", rel, t, "describe"))
    with_mit = [t for t in techniques if into[(t, "mitigates")]]
    for t in pick(with_mit, COUNTS["mitigate"]):
        # The technique page is a primary answer too: it carries the technique-specific advice for each
        # mitigation (DECISIONS D22, D24); the mitigation pages themselves are generic.
        rel = {t: 2, **{m: 2 for m in into[(t, "mitigates")]}}
        intents.append(("mitigate", f"How can I mitigate or prevent \"{docs[t].title}\"?", rel, t, "describe"))
    groups_sw = [g for g in of_type("group") if len([s for s in out[(g, "uses")] if docs[s].type == "software"]) >= 5]
    for g in pick(groups_sw, COUNTS["group_software"]):
        rel = {g: 2, **{s: 1 for s in out[(g, "uses")] if docs[s].type == "software"}}
        intents.append(("group_software", f"Which malware and tools does {docs[g].title} use?", rel, g, "keep"))
    sw_groups = [s for s in of_type("software") if len([g for g in into[(s, "uses")] if docs[g].type == "group"]) >= 2]
    for s in pick(sw_groups, COUNTS["software_groups"]):
        rel = {s: 2, **{g: 1 for g in into[(s, "uses")] if docs[g].type == "group"}}
        intents.append(("software_groups", f"Which threat groups use {docs[s].title}?", rel, s, "keep"))
    def aliases_of(doc: Document) -> list[str]:
        header = doc.text.split("\n\n", 1)[0].split("\n")
        line = next((h for h in header if h.startswith("Aliases: ")), "")
        return [a for a in line.removeprefix("Aliases: ").split(", ") if a and a != doc.title]

    aliased = [g for g in of_type("group") if aliases_of(docs[g])]
    for g in pick(aliased, COUNTS["alias"]):
        alias = rng.choice(sorted(aliases_of(docs[g])))
        intents.append(("alias", f"Which threat group is known as {alias}?", {g: 2}, g, f"keep:{alias}"))
    attributed = [c for c in of_type("campaign") if out[(c, "attributed-to")]]
    for c in pick(attributed, COUNTS["campaign"]):
        rel = {c: 2, **{g: 2 for g in out[(c, "attributed-to")]}}
        intents.append(("campaign", f"Who is behind the {docs[c].title} campaign?", rel, c, "keep"))

    def description(doc: Document) -> str:
        """MITRE's description only: the relationship lines after it name IDs, groups and software (answers)."""
        parts = doc.text.split("\n\n", 2)
        return parts[1][:500] if len(parts) > 1 else ""

    def has(term: str, text: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text.lower()) is not None

    def leak(text: str, question: str, relevance: dict[str, int], subject: str, describe: bool) -> str | None:
        """An ID or title of a relevant document that the rewrite added (absent from the template question).
        For 'describe' templates the subject's own title is a leak too: the rewrite must not name it."""
        for d in relevance:
            for term in (d, docs[d].title):
                allowed = has(term, question) and not (describe and d == subject and term == docs[d].title)
                if len(term) >= 3 and has(term, text) and not allowed:
                    return term
        return None

    cache = LLMCache()
    questions, dropped = [], []
    for n, (template, question, relevance, subject, rule) in enumerate(intents, 1):
        title = rule.split(":", 1)[1] if rule.startswith("keep:") else docs[subject].title
        rule_text = (RULE_DESCRIBE if rule == "describe" else RULE_KEEP).format(title=title)
        prompt = dict(rule=rule_text, question=question, description=description(docs[subject]), retry="")
        en = cache.ask(PARAPHRASE.format(**prompt))
        if found := leak(en, question, relevance, subject, rule == "describe"):
            en = cache.ask(PARAPHRASE.format(**{**prompt, "retry": RETRY.format(leak=found)}))
        if found := leak(en, question, relevance, subject, rule == "describe"):
            dropped.append((question, found))
            continue
        ar = cache.ask(TRANSLATE.format(question=en))
        if found := leak(ar, question, relevance, subject, rule == "describe"):  # translation can add IDs too
            ar = cache.ask(TRANSLATE.format(question=en) + RETRY.format(leak=found))
        if found := leak(ar, question, relevance, subject, rule == "describe"):
            dropped.append((question + " [ar]", found))
            ar = None
        for lang, text in (("en", en), ("ar", ar)):
            if text is None:
                continue
            questions.append({"id": f"g{n:03d}{lang}", "lang": lang, "template": template, "subject": subject,
                              "query": text, "template_question": question, "relevance": relevance})

    # ID questions: typed exactly as analysts do, no rewriting needed.
    n = len(intents)
    for t in pick(techniques, COUNTS["id"]):
        n += 1
        questions.append({"id": f"g{n:03d}mx", "lang": "mixed", "template": "id", "subject": t,
                          "query": f"ما هي التقنية {t}؟", "template_question": f"What is {t}?",
                          "relevance": {**family(t), t: 2}})
    revoked = sorted({old: d for d, doc in docs.items() for old in doc.metadata.get("previous_ids", [])}.items())
    for old, new in rng.sample(revoked, COUNTS["revoked_id"]):
        n += 1
        questions.append({"id": f"g{n:03d}mx", "lang": "mixed", "template": "revoked_id", "subject": new,
                          "query": f"ما معنى {old}؟", "template_question": f"What does {old} mean?",
                          "relevance": {new: 2}})
    for t in pick(techniques, COUNTS["bare_id"]):
        n += 1
        questions.append({"id": f"g{n:03d}mx", "lang": "mixed", "template": "bare_id", "subject": t,
                          "query": t, "template_question": t, "relevance": {**family(t), t: 2}})
    for question, found in dropped:
        print(f"dropped (answer leaked: {found!r}): {question}")
    return questions


def main() -> None:
    questions = build()
    OUT_PATH.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions), encoding="utf-8")
    by = defaultdict(int)
    for q in questions:
        by[(q["template"], q["lang"])] += 1
    print(f"{len(questions)} questions -> {OUT_PATH.relative_to(ROOT)}")
    for (template, lang), count in sorted(by.items()):
        print(f"  {template:16s} {lang:6s} {count}")


if __name__ == "__main__":
    main()
