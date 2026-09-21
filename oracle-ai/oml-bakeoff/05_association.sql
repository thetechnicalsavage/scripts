-- v1.0 | 05_association.sql | CHANGES STATE
--
-- ASSOCIATION RULES HAVE NO SCORING OPERATOR. There is no PREDICTION() for
-- them. The recommendation comes from JOINING the mined rules against what the
-- customer already holds, which means every suggestion traces to a specific
-- rule with a measured confidence and lift. That is a property worth having
-- rather than a limitation: "why was I offered this" has an answer.

declare
  v_set dbms_data_mining.setting_list;
begin
  begin dbms_data_mining.drop_model('ADDON_RULES');
  exception when others then null; end;

  v_set('ALGO_NAME')            := 'ALGO_APRIORI_ASSOCIATION_RULES';
  v_set('ASSO_MIN_SUPPORT')     := '0.01';
  v_set('ASSO_MIN_CONFIDENCE')  := '0.20';
  v_set('ODMS_ITEM_ID_COLUMN_NAME') := 'ADDON_CD';

  dbms_data_mining.create_model2(
    model_name          => 'ADDON_RULES',
    mining_function     => 'ASSOCIATION',
    data_query          => 'select subscriber_id, addon_cd from V_TRAIN_ADDONS',
    set_list            => v_set,
    case_id_column_name => 'SUBSCRIBER_ID');
end;
/

-- The recommendation: join the rules to what they already have.
select r.rule_id, r.consequent_cd as suggest, r.rule_confidence, r.rule_lift
  from V_ADDON_RULES r
 where r.antecedent_cd in (select addon_cd from SUBSCRIBER_ADDON
                            where subscriber_id = :subscriber_id)
   and r.consequent_cd not in (select addon_cd from SUBSCRIBER_ADDON
                                where subscriber_id = :subscriber_id)
 order by r.rule_lift desc
 fetch first 3 rows only;
