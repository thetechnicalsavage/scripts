-- v1.0 - spike: cost of one full collection of per-PDB metrics (V$CON_SYSMETRIC group 18, V$CON_WAITCLASSMETRIC, V$SERVICEMETRIC). Run as SYSDBA in the PDB. Result 01-Oct-2026 on ORCLPDB1: 164 rows, 19 ms CPU, 1 logical read per collection.
set feedback off serveroutput on
declare
  c0 number; c1 number; g0 number; g1 number; t0 number; t1 number; n number := 0;
  function st(p varchar2) return number is v number;
  begin select m.value into v from v$mystat m join v$statname s on s.statistic# = m.statistic# where s.name = p; return v; end;
begin
  -- warm-up, then 20 collections of the full sample: all 60-s PDB metrics, wait classes, services
  for r in (select count(*) c from v$con_sysmetric where group_id = 18) loop null; end loop;
  c0 := st('CPU used by this session'); g0 := st('session logical reads'); t0 := dbms_utility.get_time;
  for i in 1 .. 20 loop
    for r in (select metric_name, value from v$con_sysmetric where group_id = 18) loop n := n + 1; end loop;
    for r in (select wait_class#, dbtime_in_wait, time_waited from v$con_waitclassmetric) loop n := n + 1; end loop;
    for r in (select service_name, elapsedpercall, callspersec from v$servicemetric) loop n := n + 1; end loop;
  end loop;
  c1 := st('CPU used by this session'); g1 := st('session logical reads'); t1 := dbms_utility.get_time;
  dbms_output.put_line('rows per collection : '||n/20);
  dbms_output.put_line('elapsed ms / coll.  : '||round((t1-t0)*10/20, 2));
  dbms_output.put_line('cpu ms / coll.      : '||round((c1-c0)*10/20, 2)||' (centisecond resolution)');
  dbms_output.put_line('logical reads/coll. : '||round((g1-g0)/20, 1));
end;
/
exit
