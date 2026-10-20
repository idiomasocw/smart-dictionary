-- One-off cleanup of the duplicates docs/queries/duplicates.sql found (2026-10-20).
-- In Supabase's SQL Editor: select from the top through step 1 and run the selection, to
-- preview; then run the whole file. Safe to run twice: it only touches entries nobody has
-- written yet, so no student loses a published entry, and keys are retired, never deleted
-- (they're never reused).

-- The decisions. Delete a line to keep that entry; `keep` is what stays.
drop table if exists fix_retire;
create temporary table fix_retire (key text primary key, keep text, reason text);
insert into fix_retire values
  -- The same word in other capitals.
  ('en:cd:n',            'en:cd:n~2',               'CD, in capitals'),
  ('en:christian:adj',   'en:christian:adj~2',      'Christian, capitalized'),
  ('en:christian:n',     'en:christian:n~2',        'Christian, capitalized'),
  ('en:cv:n',            'en:cv:n~2',               'CV, in capitals'),
  ('en:easter:n',        'en:easter:n~2',           'Easter, capitalized'),
  ('en:tv:n',            'en:tv:n~2',               'TV, in capitals'),
  ('en:u-turn:n',        'en:u-turn:n~2',           'U-turn, capitalized'),
  ('en:u-turn:v',        'en:u-turn:v~2',           'U-turn, capitalized'),
  ('en:pin:v~2',         'en:pin:v',                'PIN is a noun only'),
  -- Spaced against hyphenated: the hyphenated entry also answers the spaced search.
  ('en:face_to_face:adv', 'en:face-to-face:adv',    'hyphen spelling'),
  ('en:high_profile:adj', 'en:high-profile:adj',    'hyphen spelling'),
  ('en:next_door:adj',    'en:next-door:adj',       'hyphen spelling'),
  ('en:ping_pong:n',      'en:ping-pong:n',         'hyphen spelling'),
  ('en:ping_pong:v',      'en:ping-pong:v',         'hyphen spelling'),
  ('en:tee_shirt:n',      'en:tee-shirt:n',         'hyphen spelling'),
  -- Without the article, the same idiom.
  ('en:great_deal:idiom', 'en:a_great_deal:idiom',  'the idiom is "a great deal"'),
  -- A preposition that's only a complement: one entry, the preposition a pattern.
  ('en:look_forward:phrv',    'en:look_forward_to:phrv',  'never used without "to"'),
  ('en:far_cry:idiom',        'en:far_cry_from:idiom',    'never used without "from"'),
  ('en:get_wind:idiom',       'en:get_wind_of:idiom',     'never used without "of"'),
  ('en:pride_oneself:phrv',   'en:pride_oneself_on:phrv', 'never used without "on"'),
  ('en:do_justice:idiom',     'en:do_justice_to:idiom',   'never used without "to"'),
  ('en:give_birth_to:idiom',  'en:give_birth:idiom',      '"to" is a complement'),
  ('en:draw_the_line_at:idiom', 'en:draw_the_line:idiom', '"at" is a complement'),
  ('en:second_nature_to:idiom', 'en:second_nature:idiom', '"to" is a complement'),
  ('en:get_even_with:idiom',  'en:get_even:idiom',        '"with" is a complement'),
  ('en:brush_up_on:phrv',     'en:brush_up:phrv',         '"on" is a complement'),
  ('en:cut_back_on:phrv',     'en:cut_back:phrv',         '"on" is a complement'),
  ('en:cut_down_on:phrv',     'en:cut_down:phrv',         '"on" is a complement'),
  ('en:follow_up_on:phrv',    'en:follow_up:phrv',        '"on" is a complement'),
  ('en:miss_out_on:phrv',     'en:miss_out:phrv',         '"on" is a complement'),
  ('en:stay_away_from:phrv',  'en:stay_away:phrv',        '"from" is a complement'),
  ('en:drop_out_of:phrv',     'en:drop_out:phrv',         '"of" is a complement'),
  ('en:keep_up_with:phrv',    'en:keep_up:phrv',          '"with" is a complement'),
  ('en:catch_up_with:phrv',   'en:catch_up:phrv',         '"with" is a complement'),
  ('en:walk_out_of:phrv',     'en:walk_out:phrv',         '"of" is a complement'),
  ('en:get_along_with:phrv',  'en:get_along:phrv',        '"with" is a complement'),
  ('en:run_out_of:phrv',      'en:run_out:phrv',          '"of" is a complement'),
  -- UK spellings seeded on their own: the US headword gets them as variants (step 3).
  ('en:carbonise:v',          'en:carbonize:v',           'UK spelling'),
  ('en:computerise:v',        'en:computerize:v',         'UK spelling'),
  ('en:crystallise:v',        'en:crystallize:v',         'UK spelling'),
  ('en:decentralise:v',       'en:decentralize:v',        'UK spelling'),
  ('en:dehumanise:v',         'en:dehumanize:v',          'UK spelling'),
  ('en:digitalisation:n',     'en:digitalization:n',      'UK spelling'),
  ('en:digitalise:v',         'en:digitalize:v',          'UK spelling'),
  ('en:economise:v',          'en:economize:v',           'UK spelling'),
  ('en:hospitalise:v',        'en:hospitalize:v',         'UK spelling'),
  ('en:humanise:v',           'en:humanize:v',            'UK spelling'),
  ('en:idolise:v',            'en:idolize:v',             'UK spelling'),
  ('en:industrialise:v',      'en:industrialize:v',       'UK spelling'),
  ('en:italicise:v',          'en:italicize:v',           'UK spelling'),
  ('en:materialisation:n',    'en:materialization:n',     'UK spelling'),
  ('en:mechanise:v',          'en:mechanize:v',           'UK spelling'),
  ('en:neutralisation:n',     'en:neutralization:n',      'UK spelling'),
  ('en:overemphasise:v',      'en:overemphasize:v',       'UK spelling'),
  ('en:personalisation:n',    'en:personalization:n',     'UK spelling'),
  ('en:tantalise:v',          'en:tantalize:v',           'UK spelling'),
  ('en:tranquilise:v',        'en:tranquilize:v',         'UK spelling');

-- 1. Preview: what each line would do. `retire` is false when the entry is written (or being
--    written) already: those stay as they are, for you to decide in the console.
select f.key, f.keep, f.reason, e.status, k.status as keep_status,
       e.published_at is null and e.status::text in ('candidate', 'queued', 'needs_repair',
                                                    'quarantined') and k.key is not null
         as retire
from fix_retire f
left join entries e on e.key = f.key
left join entries k on k.key = f.keep and k.status <> 'retired'
order by retire, f.key;

begin;

-- 2. Retire the duplicates nobody has written, when the entry kept exists.
update entries e set status = 'retired', updated_at = now()
from fix_retire f
where e.key = f.key and e.published_at is null
  and e.status::text in ('candidate', 'queued', 'needs_repair', 'quarantined')
  and exists (select from entries k where k.key = f.keep and k.status <> 'retired');

-- 3. The kept US headword lists the retired UK spelling as a variant, so a search for
--    "computerise" still finds "computerize" once its entry is written.
update seed_candidates c
set variants = c.variants || jsonb_build_array(jsonb_build_object(
      'form', uk.lemma, 'regions', jsonb_build_array('UK'))),
    updated_at = now()
from fix_retire f
join entries uk on uk.key = f.key and uk.status = 'retired'
join entries us on us.key = f.keep
where f.reason = 'UK spelling' and c.entry_id = us.id
  and not c.variants @> jsonb_build_array(jsonb_build_object('form', uk.lemma));

-- 4. Searches that pointed to a retired duplicate point nowhere now ("not found" until the
--    kept entry is written; then the lookup finds it).
update misses m set status = 'pending', entry_key = null, attempts = 0, last_error = null
from fix_retire f
join entries e on e.key = f.key and e.status = 'retired'
where m.entry_key = f.key;

commit;

-- What happened: each line's entry now.
select e.status::text, count(*) from fix_retire f join entries e on e.key = f.key group by 1;
