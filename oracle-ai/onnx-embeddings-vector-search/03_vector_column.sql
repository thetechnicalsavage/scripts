-- v1.0 | 03_vector_column.sql | CHANGES STATE
--
-- Pattern B: the vector lives on your own table, beside the row it describes.
-- Use this when the text is GENERATED FROM DATA YOU ALREADY OWN rather than
-- uploaded as a file. If there is no file, there is no reason for a
-- file-management screen.
--
-- Substitute your own model name and column widths.

create table OFFERING_VEC (
  offering_id   number(8)     not null,
  offering_cd   varchar2(30)  not null,
  line_type     varchar2(10)  not null,
  search_text   clob          not null,
  desc_vec      vector(384, FLOAT32),
  embedded_ts   timestamp(0)  default systimestamp not null,
  constraint ov_pk primary key (offering_id)
);

-- Embedding is an UPDATE. No API call, no batching against a rate limit,
-- no key to rotate, no per-token bill.
update OFFERING_VEC
   set desc_vec = vector_embedding(ALL_MINILM_L12_V2 using search_text as data)
 where desc_vec is null;
commit;
