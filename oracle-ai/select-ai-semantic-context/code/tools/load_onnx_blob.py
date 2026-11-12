#!/usr/bin/env python3
# v1.1 2026-11-11 - published copy: imports lab_conn.py from this folder instead of the lab host's own helper; no default path.
# v1.0 2026-11-11 - load a local ONNX embedding model into the login's schema on an Autonomous AI Database without
#   Object Storage: the file travels as a BLOB bind and DBMS_VECTOR.LOAD_ONNX_MODEL(model_name, model_data, metadata)
#   loads it. Idempotent: an existing model of that name is kept unless --replace. Prints no secret.
#   Needs CREATE MINING MODEL. Uses ../lab_conn.py's connect() (mTLS wallet, thick mode).
# Usage: load_onnx_blob.py --login <env file> --file <model.onnx> --model ALL_MINILM_L12_V2 [--replace]
import argparse
import hashlib
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # lab_conn.py
log = logging.getLogger('load_onnx_blob')
MODEL_RE = re.compile(r'^[A-Z][A-Z0-9_]{0,127}$')
METADATA = '{"function": "embedding", "embeddingOutput": "embedding", "input": {"input": ["DATA"]}}'


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='load an ONNX embedding model as a BLOB')
    ap.add_argument('--login', required=True)
    ap.add_argument('--file', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--replace', action='store_true')
    a = ap.parse_args(argv)
    model = a.model.upper()
    if not MODEL_RE.match(model):
        raise SystemExit(f'not a model name: {model}')
    try:
        with open(a.file, 'rb') as f:
            data = f.read()
    except OSError as e:
        raise SystemExit(f'cannot read {a.file}: {e.strerror}') from e
    log.info('model file %s: %d bytes, sha256 %s', os.path.basename(a.file), len(data), hashlib.sha256(data).hexdigest())

    import oracledb
    from lab_conn import connect, read_env
    login = read_env(a.login)
    try:
        conn, _ = connect(login)
    except oracledb.Error as e:
        raise SystemExit(f'cannot connect as {login.get("ADB_USER")}: {str(e).splitlines()[0]}') from e
    conn.action = 'load_onnx_blob'
    try:
        cur = conn.cursor()
        cur.execute('select count(*) from user_mining_models where model_name = :m', m=model)
        if cur.fetchone()[0] and not a.replace:
            log.info('model %s already loaded; kept (use --replace to reload)', model)
            return 0
        blob = conn.createlob(oracledb.DB_TYPE_BLOB)
        blob.write(data)
        cur.execute("""
            begin
              dbms_vector.drop_onnx_model(model_name => :m, force => true);
              dbms_vector.load_onnx_model(model_name => :m, model_data => :b, metadata => json(:md));
            end;""", m=model, b=blob, md=METADATA)
        cur.execute("""select model_name, algorithm, mining_function from user_mining_models
                        where model_name = :m""", m=model)
        log.info('loaded: %s', cur.fetchone())
        cur.execute(f"select vector_dims(vector_embedding({model} using 'semantic context probe' as data)) from dual")
        log.info('embedding dimensions: %s', cur.fetchone()[0])
    except oracledb.Error as e:
        log.error('load failed: %s', str(e).splitlines()[0])
        return 1
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
