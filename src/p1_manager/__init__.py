"""P1 List Manager: ongoing automation for the County List Playbook P1 presets.

See CLAUDE.md's "P1 List Manager" section and .claude/plans (Dan's machine) for
the full design. Short version: `datasift_client.py` is the one API surface
every other module goes through (auth, preset CRUD, the SAFE bulk-tag shape,
SiftLine cards); `playbook.py` reads the public County List Playbook the same
way the county-list-preset-builder skill does; `tags.py` is Dan's exact
3-tag standard; `weekly.py` (now daily) / `monthly.py` are the two
scheduled record jobs; `routing.py` puts new records on Raw / <county>;
`enrich.py` scores phones with Trestle on approval; `trestle_queue.py`
watches the Autotext tag and queues unscored leads for scoring;
`slack_app.py` is the Slack side and the HTTP app that runs it all.
"""
