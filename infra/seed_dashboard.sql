-- Demo seed for a populated Operations Dashboard.
-- Placeholders @ORG@, @PROP@, @BD@ are string-replaced before execution
-- (psql :vars do not expand inside dollar-quoted DO blocks).
--
-- Creates rooms, reservation units (arrivals/departures/in-house), room
-- conditions, a 7-day inventory occupancy trend, and folio revenue entries
-- under a single demo property. Safe to re-run (clears prior demo rows first).

DO $$
DECLARE
  v_org uuid := '@ORG@';
  v_prop uuid := '@PROP@';
  v_bd date := '@BD@';
  v_rt uuid;
  v_folio uuid;
  i int;
  v_room uuid;
  v_res uuid;
  v_unit uuid;
BEGIN
  DELETE FROM finance.folio_entries WHERE property_id = v_prop;
  DELETE FROM finance.folios WHERE property_id = v_prop;
  DELETE FROM booking.stay_room_segments WHERE property_id = v_prop;
  DELETE FROM booking.stays WHERE property_id = v_prop;
  DELETE FROM booking.room_calendar_entries WHERE property_id = v_prop;
  DELETE FROM booking.reservation_units WHERE property_id = v_prop;
  DELETE FROM booking.reservations WHERE property_id = v_prop;
  DELETE FROM booking.room_type_inventory_days WHERE property_id = v_prop;
  DELETE FROM operations.room_condition WHERE property_id = v_prop;
  DELETE FROM property.rooms WHERE property_id = v_prop;
  DELETE FROM property.room_types WHERE property_id = v_prop;

  v_rt := gen_random_uuid();
  INSERT INTO property.room_types (id, organization_id, property_id, code, name, max_adults, max_children, max_occupancy)
  VALUES (v_rt, v_org, v_prop, 'DLX', 'Deluxe Sea View', 2, 1, 3);

  FOR i IN 1..44 LOOP
    v_room := gen_random_uuid();
    INSERT INTO property.rooms (id, organization_id, property_id, room_type_id, code)
    VALUES (v_room, v_org, v_prop, v_rt, (100 + i)::text);
    INSERT INTO operations.room_condition (room_id, organization_id, property_id, cleanliness)
    VALUES (v_room, v_org, v_prop,
      CASE WHEN i <= 18 THEN 'clean'
           WHEN i <= 23 THEN 'cleaning'
           WHEN i <= 42 THEN 'dirty'
           ELSE 'clean' END);
    IF i > 42 THEN
      INSERT INTO booking.room_calendar_entries
        (id, organization_id, property_id, room_id, kind, occupied_period, status, reason)
      VALUES (gen_random_uuid(), v_org, v_prop, v_room, 'maintenance',
        tstzrange(v_bd::timestamptz, (v_bd + 3)::timestamptz, '[)'), 'active', 'AC repair');
    END IF;
  END LOOP;

  FOR i IN 0..6 LOOP
    INSERT INTO booking.room_type_inventory_days
      (organization_id, property_id, room_type_id, stay_date, physical_capacity,
       out_of_service, held_units, reserved_units, allotment_units)
    VALUES (v_org, v_prop, v_rt, v_bd - (6 - i), 44, 0, 0, (22 + i * 2), 0);
  END LOOP;

  FOR i IN 1..18 LOOP
    v_res := gen_random_uuid();
    v_unit := gen_random_uuid();
    INSERT INTO booking.reservations (id, organization_id, property_id, number, status, currency)
    VALUES (v_res, v_org, v_prop, 'CBR' || lpad((24100 + i)::text, 5, '0'), 'confirmed', 'INR');
    INSERT INTO booking.reservation_units
      (id, organization_id, property_id, reservation_id, room_type_id, arrival_date, departure_date, adults, children, status)
    VALUES (v_unit, v_org, v_prop, v_res, v_rt, v_bd, v_bd + 3, 2, 0,
            CASE WHEN i <= 6 THEN 'checked_in' ELSE 'reserved' END);
  END LOOP;

  FOR i IN 1..12 LOOP
    v_res := gen_random_uuid();
    v_unit := gen_random_uuid();
    INSERT INTO booking.reservations (id, organization_id, property_id, number, status, currency)
    VALUES (v_res, v_org, v_prop, 'CBR' || lpad((24200 + i)::text, 5, '0'), 'confirmed', 'INR');
    INSERT INTO booking.reservation_units
      (id, organization_id, property_id, reservation_id, room_type_id, arrival_date, departure_date, adults, children, status)
    VALUES (v_unit, v_org, v_prop, v_res, v_rt, v_bd - 2, v_bd, 2, 0, 'checked_in');
  END LOOP;

  v_folio := gen_random_uuid();
  INSERT INTO finance.folios (id, organization_id, property_id, type, currency, status)
  VALUES (v_folio, v_org, v_prop, 'guest', 'INR', 'open');
  FOR i IN 0..6 LOOP
    INSERT INTO finance.folio_entries
      (id, organization_id, property_id, folio_id, entry_type, amount, currency,
       business_date, source_type, source_line_key)
    VALUES (gen_random_uuid(), v_org, v_prop, v_folio, 'debit',
            (180000 + i * 17000), 'INR', v_bd - (6 - i), 'room_night',
            'seed_night:' || (v_bd - (6 - i))::text);
  END LOOP;
END $$;
