"""The layered link table as a client sees it.

The table is only useful if it survives the trip through the API unchanged, and
the one thing that must not survive is the difference between ``blocked`` and
``unknown``. A client that renders both as "failed" will page someone about a
network that was never measured -- the exact confusion the third
:class:`LayerState` exists to prevent one layer up.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from iris.api.server import EmulateResponse, LinkLayerView, LinkProfileView, _link_view
from iris.emulate.linkprobe import LayerProbe, LayerState, LinkLayer, LinkProfile


def profile(**states: LayerState) -> LinkProfile:
    return LinkProfile(
        guest_ip="192.168.0.1",
        probes=tuple(
            LayerProbe(layer, states.get(layer, LayerState.OK), f"{layer.value} detail")
            for layer in LinkLayer
        ),
    )


class TestTheView:
    def test_no_probe_is_null_not_an_empty_table(self):
        """An empty table reads as "measured, everything fine", which is a lie."""
        assert _link_view(None) is None

    def test_every_layer_is_present_in_physical_order(self):
        view = _link_view(profile())
        assert [layer.layer for layer in view.layers] == [layer.value for layer in LinkLayer]

    def test_the_three_states_survive_distinctly(self):
        view = _link_view(profile(**{
            LinkLayer.ROUTE: LayerState.OK,
            LinkLayer.ARP: LayerState.UNKNOWN,
            LinkLayer.ICMP: LayerState.BLOCKED,
        }))
        assert [layer.state for layer in view.layers] == [
            LayerState.OK, LayerState.UNKNOWN, LayerState.BLOCKED, LayerState.OK,
        ]

    def test_the_break_is_named(self):
        view = _link_view(profile(**{LinkLayer.ICMP: LayerState.BLOCKED}))
        assert view.first_break == "icmp"
        assert view.guest_ip == "192.168.0.1"

    def test_a_clean_link_names_no_break(self):
        assert _link_view(profile()).first_break == ""

    def test_an_unreadable_probe_is_carried_through(self):
        table = profile(**{LinkLayer.ICMP: LayerState.BLOCKED})
        view = _link_view(LinkProfile(
            guest_ip=table.guest_ip, probes=table.probes, unavailable="docker: not found",
        ))
        assert view.unavailable == "docker: not found"

    def test_the_view_serialises_states_as_plain_strings(self):
        data = _link_view(profile(**{LinkLayer.ARP: LayerState.BLOCKED})).model_dump(mode="json")
        assert data["first_break"] == "arp"
        assert data["layers"][1] == {"layer": "arp", "state": "blocked", "detail": "arp detail"}

    def test_an_unrecognised_state_is_rejected_rather_than_passed_through(self):
        """A closed enum, so a client cannot mistake a typo for a measurement."""
        with pytest.raises(ValidationError):
            LinkLayerView(layer="arp", state="definitely-fine")


class TestTheResponseModel:
    @staticmethod
    def minimal() -> dict[str, object]:
        return {
            "iid": 1, "success": False, "web_ok": False, "web_url": "-",
            "duration_sec": 1.0, "error": "", "container_id": "",
        }

    def test_a_run_without_a_probe_reports_link_as_null(self):
        assert EmulateResponse(**self.minimal()).link is None

    def test_link_is_optional_so_a_client_that_ignores_it_still_parses(self):
        assert "link" in EmulateResponse.model_fields
        assert EmulateResponse(**self.minimal(), link=_link_view(profile())).link is not None

    def test_a_malformed_table_is_rejected(self):
        with pytest.raises(ValidationError):
            EmulateResponse(**self.minimal(), link={"guest_ip": "10.0.0.1", "layers": "nope"})

    def test_the_profile_view_keeps_its_own_defaults(self):
        view = LinkProfileView(guest_ip="10.0.0.1")
        assert (view.first_break, view.unavailable, view.layers) == ("", "", [])