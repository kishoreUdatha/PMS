-- Seed the amenity catalogue and backfill room attributes (screens 008/059/062).
--
-- Placeholders @ORG@ and @PROP@ are string-replaced before execution
-- (psql :vars do not expand inside dollar-quoted DO blocks).
--
-- Idempotent: amenities upsert on (property_id, code); room attributes are
-- derived from the existing room code, so re-running converges to the same
-- state. Existing reservation/inventory data is never touched.

DO $$
DECLARE
  v_org  uuid := '@ORG@';
  v_prop uuid := '@PROP@';
BEGIN
  ------------------------------------------------------------------
  -- Amenity catalogue (the checkbox list on screen 059)
  ------------------------------------------------------------------
  INSERT INTO property.amenities
      (organization_id, property_id, code, name, category, icon, is_chargeable)
  VALUES
      (v_org, v_prop, 'ac',          'AC',            'comfort',       'wind',        false),
      (v_org, v_prop, 'wifi',        'Free Wi-Fi',    'technology',    'wifi',        false),
      (v_org, v_prop, 'intercom',    'Intercom',      'technology',    'phone',       false),
      (v_org, v_prop, 'tv',          'Television',    'technology',    'tv',          false),
      (v_org, v_prop, 'desk',        'Work Desk',     'general',       'briefcase',   false),
      (v_org, v_prop, 'sofa',        'Sofa',          'comfort',       'armchair',    false),
      (v_org, v_prop, 'minibar',     'Mini Bar',      'kitchen',       'wine',        true),
      (v_org, v_prop, 'safe',        'In-room Safe',  'general',       'lock',        false),
      (v_org, v_prop, 'bathtub',     'Bathtub',       'bathroom',      'bath',        false),
      (v_org, v_prop, 'kettle',      'Electric Kettle','kitchen',      'coffee',      false),
      (v_org, v_prop, 'balcony',     'Balcony',       'outdoor',       'trees',       false),
      (v_org, v_prop, 'hairdryer',   'Hair Dryer',    'bathroom',      'wind',        false),
      (v_org, v_prop, 'pool',        'Private Pool',  'outdoor',       'waves',       false),
      (v_org, v_prop, 'wheelchair',  'Wheelchair Access','accessibility','accessibility', false)
  ON CONFLICT (property_id, code) DO UPDATE
      SET name = EXCLUDED.name,
          category = EXCLUDED.category,
          icon = EXCLUDED.icon,
          is_chargeable = EXCLUDED.is_chargeable;

  ------------------------------------------------------------------
  -- Room type merchandising attributes
  ------------------------------------------------------------------
  UPDATE property.room_types SET
      base_rate = CASE code
                    WHEN 'DLX'   THEN 6500
                    WHEN 'SEA'   THEN 8500
                    WHEN 'SUITE' THEN 12000
                    WHEN 'VILLA' THEN 18000
                    ELSE COALESCE(base_rate, 6500) END,
      bed_setup = CASE code
                    WHEN 'DLX'   THEN '1 Queen Bed'
                    WHEN 'SEA'   THEN '1 King Bed'
                    WHEN 'SUITE' THEN '2 Queen Beds'
                    WHEN 'VILLA' THEN '1 King Bed'
                    ELSE bed_setup END,
      size_sqft = CASE code
                    WHEN 'DLX'   THEN 320
                    WHEN 'SEA'   THEN 420
                    WHEN 'SUITE' THEN 650
                    WHEN 'VILLA' THEN 1200
                    ELSE size_sqft END,
      description = CASE code
                    WHEN 'DLX'   THEN 'Comfortable room with garden or partial sea outlook.'
                    WHEN 'SEA'   THEN 'Full sea-facing room with private balcony.'
                    WHEN 'SUITE' THEN 'Two-bedroom family suite with living area.'
                    WHEN 'VILLA' THEN 'Standalone beachfront villa with private pool.'
                    ELSE description END
  WHERE property_id = v_prop;

  ------------------------------------------------------------------
  -- Room physical attributes, derived from the room number.
  -- First digit = floor; the wing follows from the room type.
  ------------------------------------------------------------------
  -- The floor is the room number's first digit, always. The old version
  -- collapsed anything above the third floor to '1', so rooms numbered 4xx
  -- claimed to be on the first floor.
  UPDATE property.rooms r SET
      floor = left(r.code, 1),
      -- The building follows the floor the room is on, not its room type.
      -- Naming three buildings by room type while only one building record
      -- was ever created left rooms pointing at wings that do not exist.
      building = COALESCE(
                (SELECT b.name FROM property.buildings b
                  WHERE b.property_id = v_prop
                  ORDER BY b.display_order, b.name LIMIT 1),
                'Bay View Wing'),
      housekeeping_zone = CASE
                WHEN rt.code = 'VILLA' THEN 'Villas - Beachfront'
                ELSE left(r.code, 1) || CASE left(r.code, 1)
                       WHEN '1' THEN 'st' WHEN '2' THEN 'nd'
                       WHEN '3' THEN 'rd' ELSE 'th' END
                     || ' Floor - Sea Wing' END,
      bed_setup   = COALESCE(r.bed_setup, rt.bed_setup),
      view_type   = CASE
                WHEN rt.code = 'VILLA' THEN 'Beachfront'
                WHEN rt.code = 'SEA'   THEN 'Sea View'
                WHEN rt.code = 'SUITE' THEN 'Partial Sea View'
                ELSE 'Garden View' END,
      max_adults   = rt.max_adults,
      max_children = rt.max_children,
      base_rate    = rt.base_rate,
      near_elevator = (right(r.code, 1) IN ('1', '2'))
  FROM property.room_types rt
  WHERE rt.id = r.room_type_id AND r.property_id = v_prop;

  ------------------------------------------------------------------
  -- Structured floor/building links.
  --
  -- The columns above are denormalised labels the Rooms list filters on.
  -- Buildings & Floors reads rooms.floor_id, and the seed never set it — so
  -- every room the tree could not claim showed as "0 Rooms" on its floor and
  -- was unreachable from the estate screens entirely.
  ------------------------------------------------------------------
  UPDATE property.rooms r SET
      floor_id    = f.id,
      building_id = f.building_id
  FROM property.floors f
  WHERE f.property_id = v_prop
    AND f.code = left(r.code, 1)
    AND r.property_id = v_prop;

  ------------------------------------------------------------------
  -- Amenity assignment: a common base set for every room, plus extras
  -- by room type. Replaces any prior assignment for this property.
  ------------------------------------------------------------------
  DELETE FROM property.room_amenities WHERE property_id = v_prop;

  INSERT INTO property.room_amenities (room_id, amenity_id, property_id)
  SELECT r.id, a.id, v_prop
  FROM property.rooms r
  JOIN property.room_types rt ON rt.id = r.room_type_id
  JOIN property.amenities a ON a.property_id = v_prop
  WHERE r.property_id = v_prop
    AND (
      a.code IN ('ac', 'wifi', 'tv', 'intercom', 'kettle', 'hairdryer', 'safe')
      OR (a.code = 'balcony' AND rt.code IN ('SEA', 'SUITE', 'VILLA'))
      OR (a.code = 'desk'    AND rt.code IN ('SEA', 'SUITE'))
      OR (a.code = 'sofa'    AND rt.code IN ('SUITE', 'VILLA'))
      OR (a.code = 'bathtub' AND rt.code IN ('SUITE', 'VILLA'))
      OR (a.code = 'minibar' AND rt.code IN ('SUITE', 'VILLA'))
      OR (a.code = 'pool'    AND rt.code = 'VILLA')
    )
  ON CONFLICT DO NOTHING;

  ------------------------------------------------------------------
  -- A couple of rooms out of service so the Maintenance KPI is real.
  ------------------------------------------------------------------
  UPDATE property.rooms SET service_status = 'maintenance'
  WHERE property_id = v_prop
    AND id IN (
      SELECT r.id FROM property.rooms r
      JOIN property.room_types rt ON rt.id = r.room_type_id
      WHERE r.property_id = v_prop AND rt.code = 'VILLA'
        AND NOT EXISTS (
          SELECT 1 FROM booking.reservation_units ru
          JOIN booking.stays s ON s.reservation_unit_id = ru.id
                              AND s.status = 'in_house'
          WHERE ru.assigned_room_id = r.id
        )
      ORDER BY r.code
      LIMIT 2
    );

  -- Every room needs a housekeeping row so the grid never shows NULL.
  INSERT INTO operations.room_condition
      (room_id, organization_id, property_id, cleanliness)
  SELECT r.id, v_org, v_prop, 'clean'
  FROM property.rooms r
  WHERE r.property_id = v_prop
  ON CONFLICT (room_id) DO NOTHING;
END $$;
