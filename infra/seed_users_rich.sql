-- Seed realistic User Management data matching mockup 039 (US-039, real rows).
-- Placeholder @ORG@ and @PROP@ string-replaced before execution.
DO $$
DECLARE
  v_org uuid := '@ORG@';
  v_prop uuid := '@PROP@';
  rec record;
  v_user uuid; v_mem uuid; v_dept uuid; v_role uuid; v_perm uuid;
  -- users: subject, name, email, empcode, dept_code, dept_name, status,
  --        mfa, last_login (null for invited), roles (csv)
  users_data jsonb := '[
    {"subject":"ravi.kumar","name":"Ravi Kumar","email":"ravi.kumar@chiralaresort.in","emp":"EMP001","dc":"MGMT","dn":"Management","status":"active","mfa":"enabled","last":"2025-05-26 09:14","roles":["Administrator","Resort Manager"]},
    {"subject":"anita.reddy","name":"Anita Reddy","email":"anita.reddy@chiralaresort.in","emp":"EMP023","dc":"FO","dn":"Front Office","status":"active","mfa":"enabled","last":"2025-05-26 08:47","roles":["Front Desk","Reservations"]},
    {"subject":"suresh.naidu","name":"Suresh Naidu","email":"suresh.naidu@chiralaresort.in","emp":"EMP045","dc":"HK","dn":"Housekeeping","status":"active","mfa":"disabled","last":"2025-05-25 22:32","roles":["Housekeeping"]},
    {"subject":"priya.menon","name":"Priya Menon","email":"priya.menon@chiralaresort.in","emp":"EMP067","dc":"FB","dn":"F&B Service","status":"active","mfa":"enabled","last":"2025-05-26 11:05","roles":["POS","Outlet Manager"]},
    {"subject":"arjun.varma","name":"Arjun Varma","email":"arjun.varma@chiralaresort.in","emp":"EMP082","dc":"REV","dn":"Revenue","status":"active","mfa":"enabled","last":"2025-05-24 18:21","roles":["Rates","Reports"]},
    {"subject":"neha.sharma","name":"Neha Sharma","email":"neha.sharma@chiralaresort.in","emp":"EMP109","dc":"SM","dn":"Sales & Marketing","status":"invited","mfa":"pending","last":null,"roles":["Sales","Distribution"]},
    {"subject":"vikram.singh","name":"Vikram Singh","email":"vikram.singh@chiralaresort.in","emp":"EMP134","dc":"IT","dn":"IT","status":"active","mfa":"enabled","last":"2025-05-26 07:50","roles":["IT Support","Administrator"]},
    {"subject":"karan.patel","name":"Karan Patel","email":"karan.patel@chiralaresort.in","emp":"EMP201","dc":"SEC","dn":"Security","status":"inactive","mfa":"disabled","last":"2025-05-18 13:12","roles":["Security"]}
  ]';
  u jsonb;
  rolename text;
BEGIN
  FOR u IN SELECT * FROM jsonb_array_elements(users_data) LOOP
    -- Department (idempotent).
    INSERT INTO iam.departments (id, organization_id, code, name)
    VALUES (gen_random_uuid(), v_org, u->>'dc', u->>'dn')
    ON CONFLICT (organization_id, code) DO NOTHING;
    SELECT id INTO v_dept FROM iam.departments WHERE organization_id=v_org AND code=u->>'dc';

    -- User.
    INSERT INTO iam.users (id, identity_provider, subject_id, display_name, status, mfa_status, last_login_at)
    VALUES (gen_random_uuid(), 'keycloak', u->>'subject', u->>'name', u->>'status', u->>'mfa',
            CASE WHEN u->>'last' IS NULL THEN NULL ELSE (u->>'last')::timestamptz END)
    ON CONFLICT (identity_provider, subject_id) DO UPDATE
      SET display_name=EXCLUDED.display_name, status=EXCLUDED.status,
          mfa_status=EXCLUDED.mfa_status, last_login_at=EXCLUDED.last_login_at;
    SELECT id INTO v_user FROM iam.users WHERE identity_provider='keycloak' AND subject_id=u->>'subject';

    -- Membership.
    INSERT INTO iam.memberships (id, organization_id, user_id, status)
    VALUES (gen_random_uuid(), v_org, v_user, CASE WHEN u->>'status'='invited' THEN 'invited' ELSE 'active' END)
    ON CONFLICT (organization_id, user_id) DO NOTHING;
    SELECT id INTO v_mem FROM iam.memberships WHERE organization_id=v_org AND user_id=v_user;

    -- Employee record.
    INSERT INTO iam.employees (id, organization_id, membership_id, employee_code, department_id)
    VALUES (gen_random_uuid(), v_org, v_mem, u->>'emp', v_dept)
    ON CONFLICT (membership_id) DO UPDATE SET employee_code=EXCLUDED.employee_code, department_id=EXCLUDED.department_id;

    -- Roles: ensure role exists (org), assign to membership (property scope).
    FOR rolename IN SELECT jsonb_array_elements_text(u->'roles') LOOP
      INSERT INTO iam.roles (id, organization_id, code, name, active)
      VALUES (gen_random_uuid(), v_org, upper(replace(rolename,' ','_')), rolename, true)
      ON CONFLICT (organization_id, code) DO NOTHING;
      SELECT id INTO v_role FROM iam.roles WHERE organization_id=v_org AND code=upper(replace(rolename,' ','_'));
      INSERT INTO iam.role_assignments (id, membership_id, role_id, property_id, scope_type)
      SELECT gen_random_uuid(), v_mem, v_role, v_prop, 'property'
      WHERE NOT EXISTS (SELECT 1 FROM iam.role_assignments WHERE membership_id=v_mem AND role_id=v_role AND property_id=v_prop);
    END LOOP;

    -- Active login sessions for active users (2 each) for the Active Sessions KPI.
    IF u->>'status'='active' THEN
      INSERT INTO iam.login_sessions (id, user_id, organization_id, created_at, expires_at)
      VALUES (gen_random_uuid(), v_user, v_org, now() - interval '2 hours', now() + interval '6 hours'),
             (gen_random_uuid(), v_user, v_org, now() - interval '1 day', now() + interval '5 hours')
      ON CONFLICT DO NOTHING;
    END IF;
  END LOOP;
END $$;
