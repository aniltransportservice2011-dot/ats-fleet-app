"""Proves the fast bulk _accounts_rows() returns exactly the same balances as the original per-ledger
implementation, on a big synthetic database and on a copy of the real one. Never touches fleet.db itself."""
import os, shutil, sys, tempfile, time
REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

def compare(db_path, label):
    os.environ['DATABASE_PATH'] = db_path
    import app as a
    a.DB = db_path
    with a.app.test_request_context('/'):
        conn = a.get_db()
        t0 = time.time(); fast = a._accounts_rows(conn); t_fast = time.time() - t0
        t0 = time.time(); slow = a._accounts_rows_by_entries(conn); t_slow = time.time() - t0
        conn.close()
    fk = {(r['role'], r['id']): r for r in fast}
    sk = {(r['role'], r['id']): r for r in slow}
    problems = []
    if set(fk) != set(sk):
        problems.append(f'different accounts listed: {sorted(set(fk) ^ set(sk))[:5]}')
    for k in sorted(set(fk) & set(sk)):
        if abs(fk[k]['balance'] - sk[k]['balance']) > 0.005:
            problems.append(f"{k} {fk[k]['name']}: fast {fk[k]['balance']:.2f} vs original {sk[k]['balance']:.2f}")
        for col in ('name', 'status', 'group', 'contact', 'gstin'):
            if fk[k][col] != sk[k][col]:
                problems.append(f'{k} column {col} differs')
    print(f"{label}: {len(fk)} accounts, fast {t_fast * 1000:.0f} ms vs original {t_slow * 1000:.0f} ms ({t_slow / max(t_fast, 1e-9):.0f}x faster), "
          f"{'IDENTICAL' if not problems else str(len(problems)) + ' DIFFERENCES'}")
    for p in problems[:10]:
        print('   ', p)
    return not problems

if __name__ == '__main__':
    ok = True
    lt = os.path.join(os.environ.get('LOAD_TEST_DIR') or os.path.join(tempfile.gettempdir(), 'fleet_load_test'), 'load.db')
    work = tempfile.mkdtemp(prefix='acct_equiv_')
    for label, src in (('real database copy', os.path.join(REPO, 'fleet.db')), ('12,000-trip load-test database', lt)):
        if os.path.exists(src):
            dst = os.path.join(work, os.path.basename(label) + '.db')
            shutil.copy2(src, dst)
            ok &= compare(dst, label)
    print('RESULT:', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)
