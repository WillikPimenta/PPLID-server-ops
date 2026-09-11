from __future__ import annotations

import server_ha


def test_ha_overview_blocks_automatic_failover_without_fencing():
    overview = server_ha.build_ha_overview(
        {
            "logDir": "C:/PPLID/logs",
            "databaseHa": {
                "enabled": True,
                "endpoint": "10.0.0.200",
                "port": 5432,
                "failoverMode": "automatic",
                "witness": "",
                "fencingState": "unknown",
            },
        },
        {"MAIN": {"reachable": True, "database": "ok", "ha": {"role": "primary"}}},
        machine={"nodeId": "NODE-01", "peerOpsUrl": ""},
    )
    assert overview["status"] == "blocked"
    assert overview["automaticFailoverAllowed"] is False
    assert overview["node"]["id"] == "NODE-01"


def test_ha_overview_keeps_manual_mode_explicit():
    overview = server_ha.build_ha_overview(
        {"logDir": "C:/PPLID/logs", "databaseHa": {"enabled": True, "failoverMode": "manual"}},
        {},
        machine={"nodeId": "NODE-02"},
    )
    assert overview["status"] == "manual"
    assert overview["database"]["failoverMode"] == "manual"
