# Milestone 3 — rulings made on the owner's behalf

Decisions taken during the milestone 3 build (subagent-driven development, 2026-10-18 to
2026-10-22) where the spec or plan left a choice open or conflicted. Each line: the decision,
then what it costs if it turns out wrong. Owner's own decisions are in
[plan-reviews.md](../plan-reviews.md) ("Amendments in milestone 3") and
[HANDOFF.md](../HANDOFF.md).

## Notes and chunks

1. Test PDFs are generated with reportlab at test time, not committed as binary fixtures. *If wrong:* commit a few generated PDFs later.
2. A PDF with no text at all gets the scanned-PDF (D2) message. *If wrong:* an empty but unscanned PDF shows the scanned hint.
3. Each titled slide starts a chunk; an untitled continuation slide joins the previous one, with page offsets keeping citation pages exact. *If wrong:* one citation can span two slides.
4. `ebb new --notes` also refuses more than 5 files and the same file twice before any AI call. *If wrong:* its wording differs slightly from `ebb add`'s.
5. PDF ligatures (U+FB00–FB06) are mapped to letters at extraction, not full NFKC (which turns "2¹⁰" into "210"). *If wrong:* rare ligature-like characters stay unmatched.
6. A notes file may yield 1–12 concepts (the design said 3–12). *If wrong:* a file may add a single concept.
7. The full-text cut-off `MIN_RANK` stays 0.05; on the owner's slides real matches ranked 0.6–2.3. *If wrong:* loosely related passages reach the model.

## Citations

8. The sources and citing rules are appended in code, so prompts without sources stay byte-identical. *If wrong:* some prompt text lives in Python.
9. Web mode has its own prompt file (`generate_web_questions.md`). *If wrong:* one more file to maintain.
10. Lessons show only their own verified citations; only the grade card falls back to the question's. *If wrong:* a lesson shows no quote.
11. A verified quote needs at least 3 words and 12 characters. *If wrong:* a short real quote is rejected and retried.
12. A citation retry rewrites only the failing questions and keeps the verified ones. *If wrong:* none known.
13. A retried grade keeps its first value; its explanation and misconception may update with the new citations. *If wrong:* the explanation wording changes.
14. If every provider fails during a citation retry, the batch is kept and labelled general knowledge. *If wrong:* one batch is uncited.
15. Citation numbers run continuously across the "From your notes" and "From the web" sections. *If wrong:* cosmetic.
16. Stored citation jsonb uses `kind`/`ref`/`label`/`page`/`quote` (not the spec's `type`/`id`). *If wrong:* the milestone 4 API maps two names.

## Repeats and web questions

17. Repeats are checked against the last 40 answered prompts plus this session's and the pending batch. *If wrong:* an older repeat slips through.
18. Repeat rule: Jaccard ≥ 0.6, or ≥ 0.45 with at least 3 shared content words. *If wrong:* slightly more or fewer repeats caught.
19. The avoid list's order changed in `/simplify` (it's now a superset). *If wrong:* none in practice.
20. Test fakes use non-colliding prompt text; the real repeat rule is unchanged. *If wrong:* short real questions sharing most words count as repeats.
21. Tavily errors are caught by name (never `except Exception`). *If wrong:* an unlisted error type crashes a session.
22. `requests.exceptions.RequestException` is caught without declaring `requests` (a tavily-python dependency). *If wrong:* a future tavily-python breaks the import.
23. An empty search marks the concept searched (no notice); a failed web generation drops the replacements and the session goes on. *If wrong:* no web questions for that concept this session.
24. The README explains how to trigger a web question in prose, not untested SQL. *If wrong:* forcing one takes longer.

## Disputes

25. The lesson prompt fences the student's answer in both paths (with and without notes). *If wrong:* lessons without notes read slightly differently from milestone 2.
26. A store refusal of a dispute marks the answer disputed, so `d` isn't offered again. *If wrong:* a temporary refusal can't be retried.
27. If a voided answer was a moved-on concept's only answer, that summary row is dropped. *If wrong:* the concept disappears from the summary.
28. After a dispute the terminal shows the result line and the question again, not the old grade and lesson. *If wrong:* the original explanation isn't shown twice.
29. A resumed re-check applies the stored dispute without calling the model (`Store.get_dispute`). *If wrong:* one more store method.
30. A dispute doesn't replay the correct-answer streak or the question count (the spec says routing isn't replayed). *If wrong:* one extra or one fewer question on that concept.

## Providers and the eval

31. Mistral's fallback model is `mistral-medium-latest`. *If wrong:* change `EBB_FALLBACK_MODEL`.
32. The eval writer is Mistral, so writer and grader are different model families. *If wrong:* one rebuild.
33. The committed eval set was written by Groq `qwen/qwen3.8-27b` (Mistral inactive) and graded by Groq `openai/gpt-oss-120b`. *If wrong:* shared provider blind spots inflate the 70% baseline.
34. Safety gaps go to m3-followups, not into milestone 3. *If wrong:* add a task when the owner asks.

## Compatibility and process

35. New graph state keys are read with defaults, so sessions saved by milestone 2 still resume. *If wrong:* an old session crashes on resume.
36. A session saved before `citation_failing` existed rewrites the whole batch on a citation retry. *If wrong:* harmless.
37. Tests written after the code were accepted after review checked they could fail. *If wrong:* a weak test slips through.
38. FakeTutor's prompt lists apply by position to every batch. *If wrong:* extend the fake later.
39. Terminal polish items went to m3-followups (the milestone 4 web UI replaces these flows). *If wrong:* small terminal annoyances remain.
40. `/simplify`'s larger graph restructurings are deferred to before milestone 4. *If wrong:* the graph stays harder to change.
41. Spec deltas are recorded as plan amendments; the approved spec isn't edited. *If wrong:* none.
42. The README's "Planned stack" still says shadcn until the milestone 4 design is approved. *If wrong:* briefly outdated.
