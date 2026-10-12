# v1.1 2026-10-10  Brief 14 tests: the Phoenix lock (b14lib/phoenix.py).
#   v1.1: regions in the path or query string (Codex code review r1, finding 4). v1.0: first version.
import pytest

from b14lib import phoenix
from b14lib.phoenix import RegionLockError


def test_env_region_ok_and_refused():
    phoenix.check_env_region("us-phoenix-1")
    with pytest.raises(RegionLockError):
        phoenix.check_env_region("us-chicago-1")
    with pytest.raises(RegionLockError):
        phoenix.check_env_region("")


@pytest.mark.parametrize("url", [
    "https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/routingProfiles",
    "https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/actions/chat",
    "https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com:443/openai/v1/responses",
])
def test_url_allowed(url):
    assert phoenix.check_url(url) == url


@pytest.mark.parametrize("url", [
    "http://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/routingProfiles",          # not https
    "https://generativeai.us-chicago-1.oci.oraclecloud.com/20231130/routingProfiles",         # other region
    "https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/20231130/actions/chat",  # other region
    "https://identity.us-phoenix-1.oci.oraclecloud.com/20160918/regionSubscriptions",         # not GenAI
    "https://generativeai.us-phoenix-1.oci.oraclecloud.com.example.net/x",                    # look-alike
    "https://generativeai.us-phoenix-1.oci.oraclecloud.com:8443/x",                           # other port
    "https://user:pw" + "@generativeai.us-phoenix-1.oci.oraclecloud.com/x",                   # userinfo
    "https://[::1/x",                                                                        # unparseable
    "https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/modelDiscovery?region=us-chicago-1",
    "https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/modelDiscovery?region=us-phoenix-1&region=eu-frankfurt-1",
    "https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/x/us-ashburn-1/y",
    "https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/modelDiscovery?region=us%2Dchicago%2D1",
])
def test_url_refused(url):
    with pytest.raises(RegionLockError):
        phoenix.check_url(url)


def test_phoenix_and_other_values_in_the_query_pass():
    phoenix.check_url("https://generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/modelDiscovery?"
                      "region=us-phoenix-1&capability=CHAT&modelId=cohere.command-a-03-2025&limit=1")


def test_body_other_region_anywhere_is_refused():
    phoenix.check_body({"a": ["us-phoenix-1"], "b": {"c": "us-phoenix-1"}})
    for bad in ({"regionRoutingPolicy": {"allowedRegions": ["us-phoenix-1", "us-chicago-1"]}},
                {"x": [{"y": "eu-frankfurt-2"}]},
                {"us-gov-phoenix-1": 1}):
        with pytest.raises(RegionLockError):
            phoenix.check_body(bad)


def test_body_model_ids_and_names_are_not_regions():
    phoenix.check_body({"allowedModels": ["cohere.command-a-03-2025", "meta.llama-3.3-70b-instruct",
                                          "xai.grok-4.3", "google.gemini-2.5-flash"],
                        "displayName": "b14-p1-xai-grok-4-3", "text": "Reply with exactly one word: ALPHA"})


def test_profile_body_needs_exactly_phoenix():
    ok = {"displayName": "b14-p1", "regionRoutingPolicy": {"allowedRegions": ["us-phoenix-1"]}}
    phoenix.check_profile_body(ok)
    for regions in ([], ["us-phoenix-1", "us-phoenix-1"], ["us-chicago-1"], None):
        bad = {"displayName": "b14-p1", "regionRoutingPolicy": {"allowedRegions": regions}}
        with pytest.raises(RegionLockError):
            phoenix.check_profile_body(bad)
    with pytest.raises(RegionLockError):
        phoenix.check_profile_body({"displayName": "b14-p1"})          # region policy omitted = not allowed


def test_stored_profile_check():
    phoenix.check_stored_profile({"regionRoutingPolicy": {"allowedRegions": ["us-phoenix-1"]},
                                  "modelRoutingPolicy": {"allowedModels": ["xai.grok-4.3"]}})
    with pytest.raises(RegionLockError):
        phoenix.check_stored_profile({"regionRoutingPolicy": {"allowedRegions": ["us-phoenix-1", "us-ashburn-1"]}})
    with pytest.raises(RegionLockError):
        phoenix.check_stored_profile({})
