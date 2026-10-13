-- 30_graph_queries.sql v0.3 2026-10-10 - Brief 12 step 3 (PLAN 6 row 3, section 8): one customer's memory graph through
--   SQL/PGQ on store DEMO, run as AGENTMEM by py/30_graph_sql.py (each statement starts with a "-- @<id>" marker line
--   and ends with a semicolon; binds by name). Read-only: SELECT only. These GRAPH_TABLE queries are ours; the SDK walks
--   links with relational SQL (B3, section 9).
--   v0.3: (day 1 restart) header only: reviewed with py/30_graph_sql.py v1.0 before step 3's first run (Codex record
--   19); the statements are those of v0.2.
--   v0.2: (day 1 draft) Q1-Q3 exactly as PLAN section 8 v0.1 wrote them; Q3H, the fixed-hop form the page shows beside Q3
--   (also Q3's fallback if 23.26.1 rejects the quantified pattern); Q1F and Q2F, the fallbacks for a bind variable
--   inside GRAPH_TABLE ... WHERE (the filter moves to the joined table). py/30_graph_sql.py runs Q1F/Q2F only if
--   Q1/Q2 fail, and records which form produced each result.

-- @Q1 nodes (isolated memories included)
select gt.mem_id, gt.mem_type, gt.mem_status, gt.created_at,
       dbms_lob.substr(m.content, 200, 1) as mem_text,
       json_value(m.metadata, '$."$agent_memory"."invalidation_reason"') as invalidation_reason
  from graph_table ( agentmem.demo_memory_graph
         match (n is memory)
         where n.user_id = :P1_USER_ID
         columns ( n.record_id as mem_id, n.memory_type as mem_type, n.status as mem_status,
                   n.created_at as created_at ) ) gt
  join agentmem.demo_memory m on m.record_id = gt.mem_id
 order by gt.created_at;

-- @Q2 edges
select gt.src_id, gt.rel_type, gt.rel_created, gt.dst_id, gt.dst_status,
       dbms_lob.substr(ms.content, 200, 1) as src_text,
       dbms_lob.substr(md.content, 200, 1) as dst_text
  from graph_table ( agentmem.demo_memory_graph
         match (s is memory) -[e is memory_link]-> (d is memory)
         where s.user_id = :P1_USER_ID
         columns ( s.record_id as src_id, e.relation_type as rel_type, e.created_at as rel_created,
                   d.record_id as dst_id, d.status as dst_status ) ) gt
  join agentmem.demo_memory ms on ms.record_id = gt.src_id
  join agentmem.demo_memory md on md.record_id = gt.dst_id
 order by gt.rel_created;

-- @Q3 history chain from one memory (page 2), 1-3 hops along the stored direction
select gt.dst_id, gt.hops, gt.path_types
  from graph_table ( agentmem.demo_memory_graph
         match (s is memory) -[e is memory_link]->{1,3} (d is memory)
         where s.record_id = :P2_RECORD_ID
         columns ( d.record_id as dst_id, count(e.relation_id) as hops,
                   listagg(e.relation_type, ' > ') as path_types ) ) gt
 order by gt.hops;

-- @Q3H hop-by-hop chain: three fixed patterns (1, 2 and 3 hops, distinct edge variables), union all
select 1 as hops, gt.dst_id, gt.t1 as path_types, gt.dst_id as hop1_id, cast(null as varchar2(256)) as hop2_id,
       cast(null as varchar2(256)) as hop3_id
  from graph_table ( agentmem.demo_memory_graph
         match (s is memory) -[e1 is memory_link]-> (d is memory)
         where s.record_id = :P2_RECORD_ID
         columns ( d.record_id as dst_id, e1.relation_type as t1 ) ) gt
union all
select 2, gt.dst_id, gt.t1 || ' > ' || gt.t2, gt.m1_id, gt.dst_id, null
  from graph_table ( agentmem.demo_memory_graph
         match (s is memory) -[e1 is memory_link]-> (m1 is memory) -[e2 is memory_link]-> (d is memory)
         where s.record_id = :P2_RECORD_ID
         columns ( d.record_id as dst_id, m1.record_id as m1_id, e1.relation_type as t1,
                   e2.relation_type as t2 ) ) gt
union all
select 3, gt.dst_id, gt.t1 || ' > ' || gt.t2 || ' > ' || gt.t3, gt.m1_id, gt.m2_id, gt.dst_id
  from graph_table ( agentmem.demo_memory_graph
         match (s is memory) -[e1 is memory_link]-> (m1 is memory) -[e2 is memory_link]-> (m2 is memory)
               -[e3 is memory_link]-> (d is memory)
         where s.record_id = :P2_RECORD_ID
         columns ( d.record_id as dst_id, m1.record_id as m1_id, m2.record_id as m2_id, e1.relation_type as t1,
                   e2.relation_type as t2, e3.relation_type as t3 ) ) gt
 order by 1, 2;

-- @Q1F fallback for Q1 (no bind inside GRAPH_TABLE ... WHERE; the filter is on the joined table)
select gt.mem_id, gt.mem_type, gt.mem_status, gt.created_at,
       dbms_lob.substr(m.content, 200, 1) as mem_text,
       json_value(m.metadata, '$."$agent_memory"."invalidation_reason"') as invalidation_reason
  from graph_table ( agentmem.demo_memory_graph
         match (n is memory)
         columns ( n.record_id as mem_id, n.memory_type as mem_type, n.status as mem_status,
                   n.created_at as created_at ) ) gt
  join agentmem.demo_memory m on m.record_id = gt.mem_id
 where m.user_id = :P1_USER_ID
 order by gt.created_at;

-- @Q2F fallback for Q2 (filter on the joined source table)
select gt.src_id, gt.rel_type, gt.rel_created, gt.dst_id, gt.dst_status,
       dbms_lob.substr(ms.content, 200, 1) as src_text,
       dbms_lob.substr(md.content, 200, 1) as dst_text
  from graph_table ( agentmem.demo_memory_graph
         match (s is memory) -[e is memory_link]-> (d is memory)
         columns ( s.record_id as src_id, e.relation_type as rel_type, e.created_at as rel_created,
                   d.record_id as dst_id, d.status as dst_status ) ) gt
  join agentmem.demo_memory ms on ms.record_id = gt.src_id
  join agentmem.demo_memory md on md.record_id = gt.dst_id
 where ms.user_id = :P1_USER_ID
 order by gt.rel_created;
