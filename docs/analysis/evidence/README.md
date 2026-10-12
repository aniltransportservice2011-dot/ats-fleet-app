# Evidence scripts (analysis phase, 12 Oct 2026)

Each script copies `copy.db` (a read-only online backup of `fleet.db`) to its own throw-away file, points the app at it with
`DATABASE_PATH`, blocks `requests.get/post`, then runs one experiment. None of them opens `fleet.db` for writing.

Create `copy.db` next to the scripts, then run any script:

```bash
cd docs/analysis/evidence
python3 - <<'PY'
import sqlite3
s = sqlite3.connect('file:../../../fleet.db?mode=ro', uri=True); d = sqlite3.connect('copy.db'); s.backup(d); d.close(); s.close()
PY
python3 exp_invoice.py        # IS-01  concurrent invoice numbers
python3 exp_delete.py         # DI-02  vehicle delete + invoice batch delete
python3 exp_delete2.py        # DI-02  which screens change after the delete (run after exp_delete.py is not required)
python3 exp_inv2.py           # IS-03/04  same trip twice / other party's trip
python3 exp_inv3.py           # IS-05  invoice PDF changes after trip edit
python3 exp_pay.py            # PM-01/03, DI-04, VL-01/03
python3 exp_upload.py         # UP-01  (uploads go to a scratch folder)
python3 exp_tenant.py         # SC-02
python3 exp_recon.py && python3 exp_recon2.py && python3 exp_recon3.py   # Section 5
```
The generated `exp_*.db` / `up_ins` files are scratch and can be deleted. Paths inside the scripts point at this project's folder.
