-- Seed User Management permissions (US-039) onto the PROP_ADMIN role.
-- Placeholder @ORG@ string-replaced before execution.
DO $$
DECLARE
  v_org uuid := '@ORG@';
  v_role uuid;
  v_perm uuid;
  perm text;
  perms text[] := ARRAY['user.view','user.create','user.update','user.deactivate'];
BEGIN
  SELECT id INTO v_role FROM iam.roles WHERE organization_id=v_org AND code='PROP_ADMIN';
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'PROP_ADMIN role not found for org %', v_org;
  END IF;

  FOREACH perm IN ARRAY perms LOOP
    INSERT INTO iam.permissions (id, resource_code, action_code, description)
    VALUES (gen_random_uuid(), split_part(perm,'.',1), split_part(perm,'.',2), 'User management: '||perm)
    ON CONFLICT (resource_code, action_code) DO NOTHING;

    SELECT id INTO v_perm FROM iam.permissions
    WHERE resource_code=split_part(perm,'.',1) AND action_code=split_part(perm,'.',2);

    INSERT INTO iam.role_permissions (id, role_id, permission_id, record_scope)
    VALUES (gen_random_uuid(), v_role, v_perm, 'property')
    ON CONFLICT (role_id, permission_id) DO NOTHING;
  END LOOP;
END $$;
