-- Grant role.view / role.manage to PROP_ADMIN so the Roles Matrix (US-041) is usable.
-- Placeholder @ORG@ replaced before execution.
DO $$
DECLARE
  v_org uuid := '@ORG@';
  v_role uuid; v_perm uuid; a text;
BEGIN
  SELECT id INTO v_role FROM iam.roles WHERE organization_id=v_org AND code='PROP_ADMIN';
  IF v_role IS NULL THEN RAISE EXCEPTION 'PROP_ADMIN not found'; END IF;
  FOREACH a IN ARRAY ARRAY['view','manage'] LOOP
    INSERT INTO iam.permissions (id, resource_code, action_code, description)
    VALUES (gen_random_uuid(), 'role', a, 'role.'||a)
    ON CONFLICT (resource_code, action_code) DO NOTHING;
    SELECT id INTO v_perm FROM iam.permissions WHERE resource_code='role' AND action_code=a;
    INSERT INTO iam.role_permissions (id, role_id, permission_id, record_scope)
    VALUES (gen_random_uuid(), v_role, v_perm, 'property')
    ON CONFLICT (role_id, permission_id) DO NOTHING;
  END LOOP;
END $$;

-- Rooms module grants for the Property IT Administrator role (screens 008/059/060/062).
-- Added when the Rooms cluster shipped; the Roles & Permission Matrix screen is
-- the supported way to change these going forward.
INSERT INTO iam.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM iam.roles r
JOIN iam.permissions p
  ON p.resource_code = 'rooms'
 AND p.action_code IN ('view', 'create', 'edit', 'configure', 'export')
WHERE r.name = 'Property IT Administrator'
ON CONFLICT DO NOTHING;
