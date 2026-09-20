-- Seed Approval policies + queue requests matching mockup 042 (US-042).
-- Placeholders @ORG@ and @PROP@ replaced before execution.
DO $$
DECLARE
  v_org uuid := '@ORG@';
  v_prop uuid := '@PROP@';
  v_role uuid; v_perm uuid; a text;
BEGIN
  -- Grant approval.view/decide/manage to PROP_ADMIN.
  SELECT id INTO v_role FROM iam.roles WHERE organization_id=v_org AND code='PROP_ADMIN';
  FOREACH a IN ARRAY ARRAY['view','decide','manage'] LOOP
    INSERT INTO iam.permissions (id, resource_code, action_code, description)
    VALUES (gen_random_uuid(),'approval',a,'approval.'||a) ON CONFLICT (resource_code,action_code) DO NOTHING;
    SELECT id INTO v_perm FROM iam.permissions WHERE resource_code='approval' AND action_code=a;
    IF v_role IS NOT NULL THEN
      INSERT INTO iam.role_permissions (id, role_id, permission_id, record_scope)
      VALUES (gen_random_uuid(), v_role, v_perm, 'property') ON CONFLICT (role_id,permission_id) DO NOTHING;
    END IF;
  END LOOP;

  -- Clear prior demo approvals for a clean seed.
  DELETE FROM iam.approval_decisions WHERE request_id IN (SELECT id FROM iam.approval_requests WHERE organization_id=v_org);
  DELETE FROM iam.approval_requests WHERE organization_id=v_org;
  DELETE FROM iam.approval_policies WHERE organization_id=v_org;

  -- Discount Approval Rule.
  INSERT INTO iam.approval_policies (id, organization_id, name, category, applies_to,
    initiator_roles, approver_roles, threshold_value, threshold_unit, two_level, prohibit_self_approval, active)
  VALUES (gen_random_uuid(), v_org, 'Discounts above 10%', 'discount', 'Room, Package, and Add-on Discounts',
    '["Front Office Executive","Reservations Executive","Guest Relations"]'::jsonb,
    '["Front Office Manager","General Manager"]'::jsonb, 10, '%', true, true, true);
  INSERT INTO iam.approval_policies (id, organization_id, name, category, applies_to,
    initiator_roles, approver_roles, threshold_value, threshold_unit, two_level, prohibit_self_approval, active)
  VALUES (gen_random_uuid(), v_org, 'Refunds above 5,000', 'refund', 'Folio Refunds',
    '["Guest Relations"]'::jsonb, '["General Manager"]'::jsonb, 5000, 'INR', false, true, true);
  INSERT INTO iam.approval_policies (id, organization_id, name, category, applies_to,
    initiator_roles, approver_roles, threshold_value, threshold_unit, two_level, prohibit_self_approval, active)
  VALUES (gen_random_uuid(), v_org, 'Rate overrides above 1,000', 'rate_override', 'Reservation Rate Overrides',
    '["Reservations"]'::jsonb, '["Front Office Manager"]'::jsonb, 1000, 'INR', false, true, true);
  INSERT INTO iam.approval_policies (id, organization_id, name, category, applies_to,
    initiator_roles, approver_roles, threshold_value, threshold_unit, two_level, prohibit_self_approval, active)
  VALUES (gen_random_uuid(), v_org, 'Late checkout waiver', 'other', 'Late Checkout Waivers',
    '["Housekeeping"]'::jsonb, '["Front Office Manager"]'::jsonb, 2, 'hrs', false, true, true);

  -- Pending requests matching the mockup.
  INSERT INTO iam.approval_requests (id, organization_id, property_id, category, title, entity_ref, guest_name,
    requested_by_name, requested_by_role, requested_by_subject, policy_rule_text, amount, amount_context, status, due_at)
  VALUES
   (gen_random_uuid(), v_org, v_prop, 'discount', 'Discount 12%', 'RES-7842', 'Rohit Sharma',
    'Priya Nair', 'Front Office Executive', 'priya.nair', 'Discounts > 10% require manager approval',
    12600, '12% of 1,05,000', 'pending', now() + interval '2 hours 15 minutes'),
   (gen_random_uuid(), v_org, v_prop, 'refund', 'Refund 8,500', 'FOLIO-3391', 'Ananya Iyer',
    'Sameer Khan', 'Guest Relations', 'sameer.khan', 'Refunds > 5,000 require GM approval',
    8500, '', 'pending', now() + interval '4 hours 10 minutes'),
   (gen_random_uuid(), v_org, v_prop, 'rate_override', 'Rate Override 2,000', 'RES-7876', 'Neha Kapoor',
    'Vikram Reddy', 'Reservations', 'vikram.reddy', 'Rate override > 1,000 requires manager approval',
    2000, 'New Rate: 8,000', 'pending', now() + interval '6 hours 45 minutes'),
   (gen_random_uuid(), v_org, v_prop, 'other', 'Late Checkout Waiver', 'RM-207', 'Karan Malhotra',
    'Anita Das', 'Housekeeping', 'anita.das', 'Late checkout waiver beyond 2 hours requires manager approval',
    0, 'Waiver of 2,500', 'pending', now() + interval '9 hours 20 minutes');
END $$;
