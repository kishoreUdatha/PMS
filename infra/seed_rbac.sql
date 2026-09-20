-- RBAC test data for Property Settings (US-026) verification.
-- Placeholders @ORG@ and @PROP@ string-replaced before execution.
DO $$
DECLARE
  v_org uuid := '@ORG@';
  v_prop uuid := '@PROP@';
  v_perm_view uuid; v_perm_update uuid; v_perm_audit uuid;
  v_role uuid;
  v_user_ok uuid; v_user_deny uuid;
  v_mem_ok uuid; v_mem_deny uuid;
BEGIN
  -- Permissions (global catalogue).
  INSERT INTO iam.permissions (id, resource_code, action_code, description)
  VALUES (gen_random_uuid(), 'property', 'view', 'View property settings')
  ON CONFLICT (resource_code, action_code) DO NOTHING;
  INSERT INTO iam.permissions (id, resource_code, action_code, description)
  VALUES (gen_random_uuid(), 'property', 'update', 'Update property settings')
  ON CONFLICT (resource_code, action_code) DO NOTHING;
  INSERT INTO iam.permissions (id, resource_code, action_code, description)
  VALUES (gen_random_uuid(), 'audit', 'view', 'View audit log')
  ON CONFLICT (resource_code, action_code) DO NOTHING;

  SELECT id INTO v_perm_view FROM iam.permissions WHERE resource_code='property' AND action_code='view';
  SELECT id INTO v_perm_update FROM iam.permissions WHERE resource_code='property' AND action_code='update';
  SELECT id INTO v_perm_audit FROM iam.permissions WHERE resource_code='audit' AND action_code='view';

  -- Role: Property Admin (org-owned).
  INSERT INTO iam.roles (id, organization_id, code, name, template_code, active)
  VALUES (gen_random_uuid(), v_org, 'PROP_ADMIN', 'Property IT Administrator', 'property_it_administrator', true)
  ON CONFLICT (organization_id, code) DO NOTHING;
  SELECT id INTO v_role FROM iam.roles WHERE organization_id=v_org AND code='PROP_ADMIN';

  INSERT INTO iam.role_permissions (id, role_id, permission_id, record_scope)
  VALUES (gen_random_uuid(), v_role, v_perm_view, 'property') ON CONFLICT (role_id, permission_id) DO NOTHING;
  INSERT INTO iam.role_permissions (id, role_id, permission_id, record_scope)
  VALUES (gen_random_uuid(), v_role, v_perm_update, 'property') ON CONFLICT (role_id, permission_id) DO NOTHING;
  INSERT INTO iam.role_permissions (id, role_id, permission_id, record_scope)
  VALUES (gen_random_uuid(), v_role, v_perm_audit, 'property') ON CONFLICT (role_id, permission_id) DO NOTHING;

  -- Authorized user (subject 'admin-user').
  INSERT INTO iam.users (id, identity_provider, subject_id, display_name, status)
  VALUES (gen_random_uuid(), 'keycloak', 'admin-user', 'Kishore Admin', 'active')
  ON CONFLICT (identity_provider, subject_id) DO NOTHING;
  SELECT id INTO v_user_ok FROM iam.users WHERE subject_id='admin-user';

  INSERT INTO iam.memberships (id, organization_id, user_id, status)
  VALUES (gen_random_uuid(), v_org, v_user_ok, 'active')
  ON CONFLICT (organization_id, user_id) DO NOTHING;
  SELECT id INTO v_mem_ok FROM iam.memberships WHERE organization_id=v_org AND user_id=v_user_ok;

  INSERT INTO iam.role_assignments (id, membership_id, role_id, property_id, scope_type)
  SELECT gen_random_uuid(), v_mem_ok, v_role, v_prop, 'property'
  WHERE NOT EXISTS (SELECT 1 FROM iam.role_assignments WHERE membership_id=v_mem_ok AND role_id=v_role AND property_id=v_prop);

  -- Denied user (subject 'nobody', member but no role permissions).
  INSERT INTO iam.users (id, identity_provider, subject_id, display_name, status)
  VALUES (gen_random_uuid(), 'keycloak', 'nobody-user', 'No Access', 'active')
  ON CONFLICT (identity_provider, subject_id) DO NOTHING;
  SELECT id INTO v_user_deny FROM iam.users WHERE subject_id='nobody-user';
  INSERT INTO iam.memberships (id, organization_id, user_id, status)
  VALUES (gen_random_uuid(), v_org, v_user_deny, 'active')
  ON CONFLICT (organization_id, user_id) DO NOTHING;
END $$;
