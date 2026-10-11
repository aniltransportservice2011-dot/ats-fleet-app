# Load test report — fleet app (website + mobile app)

Run: 11 Oct 2026 09:48 · test data: 12,000 trips, 4,215 payments (17,086 allocations), 4,500 maintenance bills, 2,280 salaries, 3,500 expenses, 24,000 attendance rows, 6,000 toll entries, 150 parties, 100 vendors, 160 vehicles (10.9 MB database). Machine: Mac, 8 cores. Numbers are server time only.

Standard thresholds used: **< 100 ms Excellent · < 300 ms Good · < 1 s OK · 1–3 s Slow · > 3 s Poor** (the usual web guideline is that a page should answer within ~1 s).


## Website screens

Summary: Excellent 76 · Good 8 · OK 2 · Slow 1 (of 87)

| Screen / action | Typical (median) ms | Slowest of 5 ms | SQL queries | Page size KB | Rating | HTTP |
|---|---:|---:|---:|---:|---|---|
| `/business-performance?date_from=2025-04-01&date_to=2026-10-10` | 1,023 | 1,032 | 189 | 84.3 | Slow | 200 |
| `/business-performance` | 581 | 588 | 189 | 85.1 | OK | 200 |
| `/dashboard?date_from=2025-04-01&date_to=2026-10-10` | 500 | 502 | 298 | 79.7 | OK | 200 |
| `/dashboard` | 268 | 271 | 298 | 78.3 | Good | 200 |
| `/` | 260 | 266 | 299 | 78.3 | Good | 200 |
| `/empty-runs` | 140 | 149 | 1455 | 178.7 | Good | 200 |
| `/fleet-utilization` | 138 | 144 | 1454 | 178.7 | Good | 200 |
| `/idle-tracker` | 138 | 140 | 1455 | 178.7 | Good | 200 |
| `/fleet-utilization?period=fy` | 127 | 129 | 1454 | 178.7 | Good | 200 |
| `/dashboard?date_from=2026-10-01&date_to=2026-10-10` | 110 | 112 | 298 | 76.2 | Good | 200 |
| `/dashboard?vehicle=OD11GB1054` | 100 | 101 | 298 | 76.0 | Good | 200 |
| `/maintenance?date_from=2025-04-01&date_to=2026-10-10` | 98 | 99 | 106 | 63.5 | Excellent | 200 |
| `/accounts` | 83 | 91 | 24 | 68.6 | Excellent | 200 |
| `/driver-performance` | 83 | 84 | 103 | 72.1 | Excellent | 200 |
| `/route-analytics` | 78 | 80 | 11 | 95.0 | Excellent | 200 |
| `/accounts?per_page=100` | 77 | 92 | 24 | 151.2 | Excellent | 200 |
| `/performance` | 77 | 79 | 102 | 72.1 | Excellent | 200 |
| `/accounts?role=Party` | 76 | 76 | 24 | 68.8 | Excellent | 200 |
| `/accounts?search=Steel` | 75 | 78 | 24 | 68.8 | Excellent | 200 |
| `/performance?period=fy` | 74 | 79 | 102 | 72.3 | Excellent | 200 |
| `/route-analytics?period=fy` | 74 | 75 | 11 | 95.1 | Excellent | 200 |
| `/overheads?per_page=100` | 55 | 56 | 11 | 692.9 | Excellent | 200 |
| `/maintenance?tab=tyres` | 54 | 54 | 16 | 1342.5 | Excellent | 200 |
| `/overheads` | 53 | 53 | 11 | 129.2 | Excellent | 200 |
| `/maintenance?tab=service&per_page=100` | 48 | 62 | 13 | 1112.7 | Excellent | 200 |
| `/maintenance?tab=toll&per_page=100&page=30` | 39 | 39 | 10 | 296.4 | Excellent | 200 |
| `/maintenance?tab=toll` | 38 | 39 | 10 | 98.8 | Excellent | 200 |
| `/trips?page=120&per_page=100` | 38 | 38 | 14 | 254.2 | Excellent | 200 |
| `/salaries?tab=overview&per_page=50` | 37 | 37 | 1809 | 651.6 | Excellent | 200 |
| `/overheads?status=Pending` | 36 | 37 | 11 | 129.5 | Excellent | 200 |
| `/salaries` | 35 | 36 | 1809 | 125.1 | Excellent | 200 |
| `/overheads?tab=recurring` | 32 | 33 | 11 | 129.2 | Excellent | 200 |
| `/maintenance/urea/1/edit` | 31 | 32 | 15 | 116.1 | Excellent | 200 |
| `/maintenance?tab=urea` | 27 | 28 | 14 | 116.1 | Excellent | 200 |
| `/trips?party=Party%20019%20Power%20Pvt%20Ltd` | 26 | 27 | 14 | 240.1 | Excellent | 200 |
| `/trips?per_page=100` | 25 | 26 | 14 | 505.7 | Excellent | 200 |
| `/trips?page=40` | 25 | 25 | 14 | 150.8 | Excellent | 200 |
| `/trips?search=Rourkela&per_page=100` | 24 | 24 | 14 | 507.8 | Excellent | 200 |
| `/trips` | 24 | 26 | 14 | 290.6 | Excellent | 200 |
| `/trips?vehicle=OD11GB1054` | 24 | 24 | 14 | 163.7 | Excellent | 200 |
| `/vehicles` | 24 | 24 | 12 | 140.2 | Excellent | 200 |
| `/trips?page=2` | 23 | 23 | 14 | 290.6 | Excellent | 200 |
| `/maintenance` | 23 | 23 | 106 | 63.4 | Excellent | 200 |
| `/trips?date_from=2026-04-01&date_to=2026-09-30` | 23 | 24 | 14 | 292.9 | Excellent | 200 |
| `/maintenance?tab=compliance` | 22 | 23 | 106 | 63.4 | Excellent | 200 |
| `/advances` | 20 | 20 | 8 | 374.9 | Excellent | 200 |
| `/trips?lr_number=LR-0123` | 20 | 20 | 14 | 75.0 | Excellent | 200 |
| `/maintenance?tab=battery` | 19 | 20 | 13 | 136.6 | Excellent | 200 |
| `/trips?status=active` | 18 | 19 | 14 | 291.4 | Excellent | 200 |
| `/ledger/vendor/26?per_page=100` | 18 | 18 | 120 | 153.0 | Excellent | 200 |
| `/invoice-center?invoice_type=party&party_id=19` | 18 | 18 | 230 | 346.3 | Excellent | 200 |
| `/ledger/party/19?per_page=100` | 17 | 18 | 126 | 166.0 | Excellent | 200 |
| `/ledger/vendor/26` | 17 | 17 | 120 | 92.0 | Excellent | 200 |
| `/ledger/party/19` | 16 | 16 | 126 | 104.2 | Excellent | 200 |
| `/ledger/party/19?page=5` | 15 | 15 | 126 | 104.4 | Excellent | 200 |
| `/ledger/party/19?date_from=2026-04-01&date_to=2026-09-30` | 15 | 15 | 126 | 105.0 | Excellent | 200 |
| `/invoice-center?invoice_type=vehicle_owner&vendor_id=26` | 15 | 15 | 151 | 277.2 | Excellent | 200 |
| `/vehicles/54/challans` | 14 | 14 | 15 | 63.3 | Excellent | 200 |
| `/salaries?tab=salary` | 13 | 13 | 488 | 63.6 | Excellent | 200 |
| `/vehicles/54` | 12 | 13 | 14 | 71.8 | Excellent | 200 |
| `/invoices/generated` | 12 | 12 | 7 | 473.7 | Excellent | 200 |
| `/invoices/generated?search=ATS` | 10 | 11 | 7 | 473.7 | Excellent | 200 |
| `/invoice-center` | 10 | 11 | 27 | 81.1 | Excellent | 200 |
| `/salaries?tab=attendance` | 10 | 10 | 142 | 62.0 | Excellent | 200 |
| `/trips/edit/12015` | 10 | 10 | 14 | 106.2 | Excellent | 200 |
| `/trips/add` | 10 | 10 | 12 | 106.1 | Excellent | 200 |
| `/maintenance/add` | 9 | 9 | 10 | 65.4 | Excellent | 200 |
| `/maintenance/edit/4505` | 8 | 9 | 11 | 66.6 | Excellent | 200 |
| `/employee/Employee 001` | 8 | 8 | 9 | 108.2 | Excellent | 200 |
| `/advances/add` | 8 | 8 | 6 | 49.4 | Excellent | 200 |
| `/invoices/12015` | 8 | 8 | 26 | 51.5 | Excellent | 200 |
| `/settings` | 8 | 8 | 52 | 105.3 | Excellent | 200 |
| `/invoices` | 7 | 8 | 6 | 49.0 | Excellent | 200 |
| `/invoices/12015/edit` | 7 | 8 | 9 | 50.4 | Excellent | 200 |
| `/invoices/generated/400/edit` | 7 | 8 | 9 | 61.8 | Excellent | 200 |
| `/trips/12015/view` | 7 | 8 | 8 | 59.3 | Excellent | 200 |
| `/employees/add` | 7 | 8 | 6 | 49.1 | Excellent | 200 |
| `/maintenance/4505/view` | 7 | 7 | 7 | 51.2 | Excellent | 200 |
| `/overheads/add` | 7 | 8 | 6 | 49.2 | Excellent | 200 |
| `/payment/party/19` | 7 | 7 | 7 | 49.1 | Excellent | 200 |
| `/vehicles/edit/54` | 7 | 7 | 7 | 51.7 | Excellent | 200 |
| `/payment/vendor/26` | 7 | 7 | 7 | 49.1 | Excellent | 200 |
| `/payments/4230/view` | 7 | 8 | 11 | 51.7 | Excellent | 200 |
| `/vehicles/54/rc-lookup` | 7 | 7 | 7 | 49.4 | Excellent | 200 |
| `/vehicles/add` | 7 | 7 | 6 | 51.2 | Excellent | 200 |
| `/salaries/1/payslip` | 4 | 4 | 4 | 2.0 | Excellent | 200 |
| `/app-manifest.json` | 0 | 0 |  | 0.5 | Excellent | 200 |

## Mobile app screens

Summary: Excellent 31 · Good 2 · OK 3 (of 36)

| Screen / action | Typical (median) ms | Slowest of 5 ms | SQL queries | Page size KB | Rating | HTTP |
|---|---:|---:|---:|---:|---|---|
| `/app/analysis` | 345 | 361 | 68 | 103.6 | OK | 200 |
| `/app/business-performance` | 341 | 348 | 67 | 103.6 | OK | 200 |
| `/app/business-performance?period=fy` | 314 | 319 | 67 | 103.6 | OK | 200 |
| `/app/dashboard` | 203 | 206 | 98 | 124.5 | Good | 200 |
| `/app/fleet-utilization` | 139 | 140 | 1455 | 105.7 | Good | 200 |
| `/app/ledger` | 83 | 83 | 25 | 107.8 | Excellent | 200 |
| `/app/performance` | 82 | 83 | 103 | 103.2 | Excellent | 200 |
| `/app/ledger?show=100` | 76 | 77 | 25 | 149.2 | Excellent | 200 |
| `/app/performance?period=fy` | 74 | 75 | 103 | 103.2 | Excellent | 200 |
| `/app/analytics` | 67 | 68 | 11 | 111.9 | Excellent | 200 |
| `/app/analytics?period=fy` | 60 | 62 | 11 | 112.0 | Excellent | 200 |
| `/app/expenses` | 41 | 51 | 11 | 141.6 | Excellent | 200 |
| `/app/tyres` | 39 | 40 | 14 | 408.7 | Excellent | 200 |
| `/app/expenses?show=100` | 38 | 39 | 11 | 221.3 | Excellent | 200 |
| `/app/maintenance?tab=service&show=100` | 31 | 31 | 13 | 328.6 | Excellent | 200 |
| `/app/urea` | 29 | 30 | 15 | 136.4 | Excellent | 200 |
| `/app/toll` | 28 | 28 | 11 | 134.3 | Excellent | 200 |
| `/app/maintenance` | 24 | 24 | 107 | 105.4 | Excellent | 200 |
| `/app/vehicles` | 19 | 20 | 13 | 134.0 | Excellent | 200 |
| `/app/trips?show=500` | 19 | 19 | 11 | 587.4 | Excellent | 200 |
| `/app/battery` | 19 | 19 | 13 | 143.0 | Excellent | 200 |
| `/app/ledger/vendor/26` | 17 | 18 | 121 | 125.5 | Excellent | 200 |
| `/app/trips/12015/edit` | 17 | 17 | 15 | 83.5 | Excellent | 200 |
| `/app/ledger/party/19` | 17 | 17 | 127 | 141.9 | Excellent | 200 |
| `/app/trips/add` | 16 | 17 | 13 | 83.7 | Excellent | 200 |
| `/app/ledger/party/19?show=100` | 16 | 16 | 127 | 203.3 | Excellent | 200 |
| `/app/trips?show=100` | 13 | 14 | 11 | 197.2 | Excellent | 200 |
| `/app/salary` | 13 | 14 | 489 | 118.4 | Excellent | 200 |
| `/app/salary?show=100` | 13 | 14 | 489 | 195.6 | Excellent | 200 |
| `/app/vehicles/54` | 13 | 13 | 15 | 95.8 | Excellent | 200 |
| `/app/trips` | 13 | 14 | 11 | 114.9 | Excellent | 200 |
| `/app/compliance` | 8 | 8 | 11 | 123.0 | Excellent | 200 |
| `/app/employee/Employee 001` | 8 | 8 | 10 | 108.5 | Excellent | 200 |
| `/app/trips/12015` | 8 | 8 | 9 | 104.3 | Excellent | 200 |
| `/app/profile` | 7 | 8 | 8 | 104.9 | Excellent | 200 |
| `/app/salary/attendance` | 7 | 8 | 7 | 92.5 | Excellent | 200 |

## Downloads (Excel / PDF)

Summary: Excellent 10 · Good 4 · OK 5 · Slow 1 · Poor 1 (of 21)

| Screen / action | Typical (median) ms | Slowest of 5 ms | SQL queries | Page size KB | Rating | HTTP |
|---|---:|---:|---:|---:|---|---|
| `/trips/export/pdf` | 5,799 | 5,928 | 2 | 1362.2 | Poor | 200 |
| `/export` | 1,321 | 1,344 | 4 | 1339.5 | Slow | 200 |
| `/maintenance/export/pdf` | 941 | 963 | 2 | 383.6 | OK | 200 |
| `/trips/export` | 670 | 694 | 2 | 633.0 | OK | 200 |
| `/ledger/party/19/export/pdf` | 550 | 570 | 138 | 128.3 | OK | 200 |
| `/business-performance/export?date_from=2025-04-01&date_to=2026-10-10` | 358 | 360 | 27 | 12.1 | OK | 200 |
| `/ledger/vendor/26/export/pdf` | 301 | 317 | 129 | 69.3 | OK | 200 |
| `/overheads/export` | 164 | 200 | 2 | 125.3 | Good | 200 |
| `/route-analytics/export` | 152 | 188 | 3 | 132.8 | Good | 200 |
| `/maintenance/export` | 135 | 172 | 2 | 143.8 | Good | 200 |
| `/business-performance/export` | 101 | 103 | 27 | 11.1 | Good | 200 |
| `/accounts/export` | 92 | 95 | 19 | 20.0 | Excellent | 200 |
| `/ledger/party/19/export` | 69 | 70 | 120 | 52.3 | Excellent | 200 |
| `/ledger/vendor/26/export` | 46 | 47 | 111 | 31.1 | Excellent | 200 |
| `/fleet-utilization/export` | 41 | 42 | 272 | 8.3 | Excellent | 200 |
| `/performance/export` | 22 | 23 | 93 | 14.4 | Excellent | 200 |
| `/invoices/generated/400/pdf/preview` | 13 | 13 | 26 | 5.6 | Excellent | 200 |
| `/invoices/generated/400/pdf` | 12 | 12 | 26 | 5.6 | Excellent | 200 |
| `/vehicles/54/rc-lookup/download` | 9 | 9 | 9 | 49.4 | Excellent | 200 |
| `/invoices/12015/pdf` | 5 | 5 | 22 | 2.4 | Excellent | 200 |
| `/settings/users/export` | 4 | 4 | 2 | 4.9 | Excellent | 200 |

## Buttons that save data

Summary: Excellent 12 · Good 1 (of 13)

| Screen / action | Typical (median) ms | Slowest of 5 ms | SQL queries | Page size KB | Rating | HTTP |
|---|---:|---:|---:|---:|---|---|
| `Invoice PDF preview (200 trips)` | 106 | 107 |  |  | Good | 200 |
| `Invoice PDF preview (batch of 25 trips)` | 20 | 21 |  |  | Excellent | 200 |
| `Add vendor payment (trips + maintenance + bills)` | 4 | 4 |  |  | Excellent | 302 |
| `Add Trip (save)` | 4 | 5 |  |  | Excellent | 302 |
| `Add Trip — mobile app (save)` | 4 | 4 |  |  | Excellent | 302 |
| `Edit a trip (save)` | 4 | 4 |  |  | Excellent | 302 |
| `Add Hired Trip (save)` | 4 | 4 |  |  | Excellent | 302 |
| `Add party payment — nothing ticked (auto-allocates across ~hundreds of trips)` | 3 | 3 |  |  | Excellent | 302 |
| `Add party payment — mobile app` | 3 | 3 |  |  | Excellent | 200 |
| `Add expense (save)` | 2 | 3 |  |  | Excellent | 302 |
| `Add maintenance bill (save)` | 2 | 3 |  |  | Excellent | 302 |
| `Add salary payment (save)` | 2 | 3 |  |  | Excellent | 302 |
| `Add advance (save)` | 2 | 2 |  |  | Excellent | 302 |

## Many people at once (real server, same settings as Render: 2 workers × 4 threads)

Each virtual user logs in, then clicks around like a person would (1–2 clicks a second at most, ~10% of clicks are saves).

| Users at once | Requests | Requests/sec | Typical (p50) ms | p95 ms | p99 ms | Slowest ms | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 60 | 1.5 | 76 | 313 | 415 | 562 | 0 |
| 5 | 284 | 7.0 | 60 | 320 | 928 | 1,732 | 0 |
| 10 | 413 | 10.1 | 73 | 1,726 | 2,900 | 5,733 | 0 |
| 25 | 330 | 7.5 | 1,783 | 6,062 | 8,218 | 11,471 | 0 |
| 50 | 234 | 5.1 | 5,637 | 15,091 | 18,442 | 18,806 | 0 |

Slowest screens under the heaviest load:

- `/salaries` — typical 8,435 ms, p95 16,502 ms (6 requests)
- `POST /overheads/add` — typical 12,782 ms, p95 16,203 ms (6 requests)
- `/app/trips` — typical 6,196 ms, p95 15,816 ms (14 requests)
- `POST /payment/party/19` — typical 8,783 ms, p95 15,522 ms (8 requests)
- `/trips` — typical 4,568 ms, p95 14,843 ms (55 requests)
- `POST /trips/add` — typical 6,782 ms, p95 14,297 ms (10 requests)
