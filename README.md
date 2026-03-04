More to come... 

## Admin Tools

### Managing User Claims (`scripts/set_claims.py`)

Firebase custom claims are the sole source of authorization in this app.
All role and patient access assignments are made via this CLI script.

**Assign a role and patient access:**
```bash
python scripts/set_claims.py --email user@example.com --role caregiver --patient-ids 1 3
python scripts/set_claims.py --email user@example.com --role admin
python scripts/set_claims.py --uid <firebase-uid> --role clinician --patient-ids 2
```

**View current claims:**
```bash
python scripts/set_claims.py --email user@example.com --show
```

**Clear all claims (reset to new user state):**
```bash
python scripts/set_claims.py --email user@example.com --clear
```

Clearing claims is useful for:
- Resetting demo accounts between workshop sessions
- Resetting a test account to re-trigger the auto-bootstrap welcome flow
- Removing access from a user without deleting their account

> **Note:** After setting or clearing claims, the user must sign out
> and back in for the new claims to appear in their token.

**Available roles:** `patient` | `caregiver` | `clinician` | `admin`

---

### Auto-Bootstrap for New Users

When a user signs in for the first time with no existing claims, the
app automatically assigns them:
- Role: `patient`
- Patient access: one randomly selected patient (1–5)

They will see a welcome modal prompting them to sign out and back in
to activate their access. To get access to additional patients or an
elevated role, they should contact `admin@ai-wtf.xyz`.

To re-trigger the bootstrap flow for an existing account (e.g. for
demo purposes), clear their claims first:
```bash
python scripts/set_claims.py --email user@example.com --clear
```
Then have them sign out and back in.

---

### Runtime Security Controls

Security controls can be toggled at runtime via the `/settings` page
(admin only) or via the API:
```bash
# View current config
curl http://localhost:8080/api/admin/security-config \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"

# Update a control
curl -X PUT http://localhost:8080/api/admin/security-config \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"authorization_required": false}'

# Reset all controls to config.yaml defaults
curl -X POST http://localhost:8080/api/admin/security-config/reset \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

Runtime changes are **in-memory only** — they reset to `config.yaml`
defaults on server restart.
