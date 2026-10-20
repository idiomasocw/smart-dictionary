-- Duplicate audit for the dictionary's entries. Read only: run any block in Supabase's SQL
-- Editor (or psql) against production. Each block lists pairs to look at, not verdicts:
-- docs/03-system-design.md §3.6 says which kinds are expected and which to retire.

-- 1. The same headword, part of speech and kind more than once (homographs ~2, ~3). Expected
--    when the capitals mean another word (Pole/pole, March/march); a duplicate when they don't
--    (cd/CD).
select lemma_norm, pos, kind::text,
       array_agg(lemma || '  ' || key || '  ' || array_to_string(sources, ',') order by homograph)
from entries where status <> 'retired'
group by lemma_norm, pos, kind having count(*) > 1 order by 1;

-- 2. The same headword and part of speech as two kinds (idiom and phrase): always a duplicate.
select lemma_norm, pos, array_agg(key || '  ' || array_to_string(sources, ',') order by key)
from entries where status <> 'retired'
group by lemma_norm, pos having count(distinct kind) > 1 order by 1;

-- 3. A headword with and without a leading article (good deal / a good deal). Often two real
--    expressions; a duplicate when they mean the same.
with e as (select key, lemma_norm, pos from entries where status <> 'retired')
select a.key, b.key from e a
join e b on regexp_replace(b.lemma_norm, '^(a|an|the) ', '') = a.lemma_norm
        and b.lemma_norm <> a.lemma_norm
order by 1;

-- 4. Hyphen against space (well-known / well known): one entry, the other spelling a variant.
with e as (select key, lemma_norm, pos from entries where status <> 'retired')
select a.key, b.key from e a
join e b on replace(b.lemma_norm, '-', ' ') = a.lemma_norm and b.lemma_norm <> a.lemma_norm
        and b.pos = a.pos
order by 1;

-- 5. A multiword headword with and without a trailing preposition (a great deal / a great
--    deal of): one entry, the preposition a pattern.
with e as (select key, lemma_norm, pos, kind from entries where status <> 'retired')
select a.key, b.key from e a
join e b on regexp_replace(b.lemma_norm, ' (of|to|with|for|on|in|at|about|from)$', '')
            = a.lemma_norm
        and b.lemma_norm <> a.lemma_norm and b.pos = a.pos
where a.kind <> 'word'
order by 1;

-- 6. A headword that's another entry's spelling variant (colour, as an entry, while color
--    lists it): retire it.
with e as (select id, key, lemma_norm, pos from entries where status <> 'retired'),
     variant as (
  select e.key, e.pos, lower(v->>'form') as form
  from seed_candidates c join e on e.id = c.entry_id, jsonb_array_elements(c.variants) v)
select e.key as entry, variant.key as variant_of, variant.form
from e join variant on variant.form = e.lemma_norm and variant.key <> e.key
                   and variant.pos = e.pos
order by 1;

-- 7. US and UK spellings both seeded as entries, not linked as variants (realize/realise,
--    color/colour, center/centre).
with e as (select key, lemma_norm, pos, kind from entries where status <> 'retired')
select a.key, b.key from e a
join e b on b.pos = a.pos and b.kind = a.kind and b.key <> a.key and b.lemma_norm in (
  regexp_replace(a.lemma_norm, 'ize$', 'ise'),
  regexp_replace(a.lemma_norm, 'ization$', 'isation'),
  regexp_replace(a.lemma_norm, 'or$', 'our'),
  regexp_replace(a.lemma_norm, 'er$', 're'))
where a.lemma_norm ~ '(ize|ization|or|er)$'
order by 1;

-- 8. Leftover notation in headwords: parentheses or slashes should have become variants or
--    patterns when the lists were normalized.
select key, lemma from entries where status <> 'retired' and lemma ~ '[()/]' order by 1;
