-- Duplicate audit for the dictionary's entries. Read only: run any block in Supabase's SQL
-- Editor (or psql) against production. Each block lists pairs to look at, not verdicts:
-- docs/03-system-design.md §3.6 says which kinds are expected and which to retire.
-- Every pair shows both keys with their status, and `approved` false marks an AI entry
-- from before reviews: decide those in the console's Word requests, not here.

-- 1. The same headword, part of speech and kind more than once (homographs ~2, ~3). Expected
--    when the capitals mean another word (Pole/pole, March/march); a duplicate when they don't
--    (cd/CD).
select lemma_norm, pos, kind::text,
       array_agg(lemma || '  ' || key || '  ' || status::text || '  '
                 || array_to_string(sources, ',') order by homograph) as entries
from entries where status <> 'retired'
group by lemma_norm, pos, kind having count(*) > 1 order by 1;

-- 2. The same headword and part of speech as two kinds (idiom and phrase): always a duplicate.
select lemma_norm, pos,
       array_agg(key || '  ' || status::text || '  ' || array_to_string(sources, ',')
                 order by key) as entries
from entries where status <> 'retired'
group by lemma_norm, pos having count(distinct kind) > 1 order by 1;

-- 3. A headword with and without a leading article (good deal / a good deal). Often two real
--    expressions; a duplicate when they mean the same.
with e as (select key, lemma_norm, pos, status::text as status, approved_at is not null
                  as approved from entries where status <> 'retired')
select a.key as without_article, a.status as its_status, a.approved as its_approved,
       b.key as with_article, b.status as with_status, b.approved as with_approved
from e a
join e b on regexp_replace(b.lemma_norm, '^(a|an|the) ', '') = a.lemma_norm
        and b.lemma_norm <> a.lemma_norm
order by 1, 4;

-- 4. Hyphen against space (well-known / well known): one entry, the other spelling a variant.
with e as (select key, lemma_norm, pos, status::text as status, approved_at is not null
                  as approved from entries where status <> 'retired')
select a.key as spaced, a.status as spaced_status, b.key as hyphenated,
       b.status as hyphenated_status
from e a
join e b on replace(b.lemma_norm, '-', ' ') = a.lemma_norm and b.lemma_norm <> a.lemma_norm
        and b.pos = a.pos
order by 1;

-- 5. A multiword headword with and without a trailing preposition (brush up / brush up on).
--    One entry with the preposition as a pattern when it's only a complement; two entries
--    when it changes the meaning (go out / go out with).
with e as (select key, lemma_norm, pos, kind, status::text as status from entries
           where status <> 'retired')
select a.key as shorter, a.status as shorter_status, b.key as longer,
       b.status as longer_status
from e a
join e b on regexp_replace(b.lemma_norm, ' (of|to|with|for|on|in|at|about|from)$', '')
            = a.lemma_norm
        and b.lemma_norm <> a.lemma_norm and b.pos = a.pos
where a.kind <> 'word'
order by 1;

-- 6. A headword that's another entry's spelling variant (colour, as an entry, while color
--    lists it): retire it.
with e as (select id, key, lemma_norm, pos, status::text as status from entries
           where status <> 'retired'),
     variant as (
  select e.key, e.pos, lower(v->>'form') as form
  from seed_candidates c join e on e.id = c.entry_id, jsonb_array_elements(c.variants) v)
select e.key as entry, e.status, variant.key as variant_of, variant.form
from e join variant on variant.form = e.lemma_norm and variant.key <> e.key
                   and variant.pos = e.pos
order by 1;

-- 7. US and UK spellings both seeded as entries, not linked as variants (realize/realise,
--    color/colour, center/centre). A few pairs are different words (timber/timbre) or UK
--    spellings kept on purpose (metre, which covers only some meanings of meter).
with e as (select key, lemma_norm, pos, kind, status::text as status from entries
           where status <> 'retired'),
     uk as (
  select key, pos, kind,
         case when lemma_norm ~ 'ize$' then regexp_replace(lemma_norm, 'ize$', 'ise')
              when lemma_norm ~ 'ization$' then regexp_replace(lemma_norm, 'ization$', 'isation')
              when lemma_norm ~ 'yze$' then regexp_replace(lemma_norm, 'yze$', 'yse')
              when lemma_norm ~ '[^aeiou]or$' then regexp_replace(lemma_norm, 'or$', 'our')
              when lemma_norm ~ '(t|b|v)er$' then regexp_replace(lemma_norm, 'er$', 're')
         end as spelling
  from e)
select uk.key as us, us_entry.status as us_status, b.key as uk, b.status as uk_status
from uk
join e us_entry on us_entry.key = uk.key
join e b on b.lemma_norm = uk.spelling and b.pos = uk.pos and b.kind = uk.kind
order by 1;

-- 8. Leftover notation in headwords: parentheses or slashes should have become variants or
--    patterns when the lists were normalized.
select key, lemma from entries where status <> 'retired' and lemma ~ '[()/]' order by 1;
