-- Owner reviews, for Supabase's SQL Editor (2026-10-20). Blocks 1, 3 and 5 only read. Blocks
-- 2, 4 and 6 change entries: run each on its own, by selecting it. Block 2 is for after the
-- deploy that exempts a headword from its own V10; blocks 5 and 6 need migration 0013.

-- 1. Quarantined entries and why: the checks their latest submission failed.
select e.key, e.cefr, r.created_at,
       array(select distinct err->>'check' from jsonb_array_elements(r.qa->'errors') err)
         as failed_checks,
       array(select err->>'message' from jsonb_array_elements(r.qa->'errors') err limit 3)
         as first_problems
from entries e
join lateral (select qa, created_at from entry_revisions
              where entry_id = e.id order by created_at desc limit 1) r on true
where e.status = 'quarantined'
order by e.priority desc;

-- 2. Requeue the ones only V10 stopped (kill: its examples held the headword itself). Each
--    gets its attempts back; the next runs write it with the fixed check. The console's
--    Requeue button does the same for one entry at a time.
update entries e set status = 'queued', attempts = 0, updated_at = now()
where e.status = 'quarantined' and e.published_at is null
  and (select bool_and(err->>'check' = 'V10')
       from jsonb_array_elements((select qa->'errors' from entry_revisions
                                  where entry_id = e.id order by created_at desc limit 1)) err)
returning e.key;

-- 3. Seeded headwords that hold a word on the safety list. Each one is your choice (seeded),
--    so the routine now writes it, with calm, non-graphic examples. Retire any you don't want
--    for 16+ learners, in the console or with:
--      update entries set status = 'retired', updated_at = now() where key = '<key>';
select key, cefr, status::text
from entries
where status <> 'retired'
  and string_to_array(lemma_norm, ' ') && array['sex', 'sexy', 'sexual', 'porn', 'porno',
    'pornography', 'naked', 'nude', 'orgasm', 'rape', 'raped', 'rapist', 'murder', 'murdered',
    'murderer', 'kill', 'killed', 'killing', 'suicide', 'torture', 'terrorist', 'terrorism',
    'bomb', 'cocaine', 'heroin', 'meth', 'drunk', 'nazi', 'genocide']
order by cefr nulls last, key;

-- 4. A suggested first cut from block 3 (2026-10-20): explicit sexual terms, wording that
--    guidelines advise against (commit suicide), slang and odd tags with little value for
--    learners. Delete any line you want to keep, then run. Only entries nobody has written are
--    retired; a written one stays, for the console's Retire.
update entries set status = 'retired', updated_at = now()
where published_at is null
  and status::text in ('candidate', 'queued', 'needs_repair', 'quarantined')
  and key in (
    'en:child_pornography:n',
    'en:oral_sex:n',
    'en:sex_toy:n',
    'en:orgasm:n',
    'en:porn:n',
    'en:porno:n',
    'en:have_sex:v',
    'en:sexual_intercourse:n',
    'en:sex_object:n',
    'en:sex_change:n',
    'en:date_rape:n',
    'en:statutory_rape:n',
    'en:kill_oneself:phrv',
    'en:commit_suicide:v',
    'en:suicide_pact:n',
    'en:meth:n',
    'en:stark_naked:n'
  )
returning key;

-- 5. One multiword expression with two approved entries: another kind (miss the boat as an
--    idiom and as a verb) or with and without its article (the last straw, last straw). One
--    headword of one kind under two parts of speech (a few, determiner and pronoun) or as two
--    homographs (big brother, Big Brother) is left out: those are two entries on purpose.
--    Keep the written one of each pair. A good deal and good deal: decide whether they mean
--    the same.
select expression_of(a.lemma_norm) as expression,
       a.key as first_key, a.status::text as first_status,
       a.published_at is not null as first_written,
       b.key as second_key, b.status::text as second_status,
       b.published_at is not null as second_written
from entries a
join entries b on b.language = 'en' and b.key > a.key
              and expression_of(b.lemma_norm) = expression_of(a.lemma_norm)
where a.language = 'en' and strpos(a.lemma_norm, ' ') > 0
  and a.status <> 'retired' and b.status <> 'retired'
  and a.approved_at is not null and b.approved_at is not null
  and (a.lemma_norm <> b.lemma_norm or a.kind <> b.kind)
order by 1;

-- 6. Retire the unwritten one of the two pairs block 5 found on 2026-10-20. The idioms stay:
--    AI wrote them and you approved them. The console's Retire does the same, one at a time;
--    searches that pointed to these keys find the idioms on their next lookup.
update entries set status = 'retired', updated_at = now()
where published_at is null and status::text in ('candidate', 'queued')
  and key in ('en:miss_the_boat:v', 'en:last_straw:idiom')
returning key;
