"""P1 List Manager: ongoing automation for the County List Playbook P1 presets.

See CLAUDE.md's "P1 List Manager" section and .claude/plans (Dan's machine) for
the full design. Short version: `datasift_client.py` is the one API surface
every other module goes through (auth, preset CRUD, the SAFE bulk-tag shape,
SiftLine cards); `playbook.py` reads the public County List Playbook the same
way the county-list-preset-builder skill does; `tags.py` is Dan's exact
3-tag standard; `weekly.py` / `monthly.py` are the two scheduled jobs.
"""
