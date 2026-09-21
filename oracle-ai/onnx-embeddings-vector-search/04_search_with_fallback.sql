-- v1.0 | 04_search_with_fallback.sql | READ-ONLY
--
-- The search, and the two details worth copying.
--
-- 1. The WHERE clause. This is what a document index cannot do: the same
--    statement filters on the row's own columns while it ranks on distance.
-- 2. FETCH APPROXIMATE uses the vector index when one is available. The
--    exception handler repeats the query WITHOUT it, so a missing or
--    rebuilding index degrades to slower-and-correct instead of an error on
--    the screen.

create or replace function offering_search (
  p_question  in varchar2,
  p_line_type in varchar2 default null,
  p_top_n     in number   default 10
) return sys_refcursor is
  l_cur sys_refcursor;
begin
  open l_cur for
    select v.offering_cd,
           round(1 - v.dist, 4) as score,
           dbms_lob.substr(v.search_text, 110, 1) as snippet
      from (select o.*,
                   vector_distance(
                     o.desc_vec,
                     vector_embedding(ALL_MINILM_L12_V2 using p_question as data),
                     COSINE) as dist
              from OFFERING_VEC o
             where o.desc_vec is not null
               and (p_line_type is null or o.line_type in (p_line_type, 'BOTH'))
             order by dist
             fetch approximate first p_top_n rows only) v;
  return l_cur;
exception
  when others then
    -- Exact fallback: identical query, no APPROXIMATE.
    open l_cur for
      select v.offering_cd,
             round(1 - v.dist, 4) as score,
             dbms_lob.substr(v.search_text, 110, 1) as snippet
        from (select o.*,
                     vector_distance(
                       o.desc_vec,
                       vector_embedding(ALL_MINILM_L12_V2 using p_question as data),
                       COSINE) as dist
                from OFFERING_VEC o
               where o.desc_vec is not null
                 and (p_line_type is null or o.line_type in (p_line_type, 'BOTH'))
               order by dist
               fetch first p_top_n rows only) v;
    return l_cur;
end offering_search;
/
