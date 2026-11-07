from __future__ import annotations

import pytest
from engine.config import REPO_ROOT
from engine.gateway.routes import RouteMatcher
from engine.manifest import CompanyManifest, RouteSpec, load_manifest
from pydantic import ValidationError


def test_starter_manifest_routes() -> None:
    manifest = load_manifest(REPO_ROOT / "content/companies/starter")
    names = {r.name for r in manifest.routes}
    assert {"health", "hello", "login", "search", "list_items", "delete_item"} <= names
    matcher = RouteMatcher(manifest.routes)
    assert matcher.match("GET", "/health") == "health"
    assert matcher.match("GET", "/hello") == "hello"
    assert matcher.match("POST", "/login") == "login"
    assert matcher.match("GET", "/search") == "search"
    assert matcher.match("GET", "/items") == "list_items"
    assert matcher.match("DELETE", "/items/123") == "delete_item"


def test_route_spec_method_normalization() -> None:
    r = RouteSpec(name="test", method="get", path="/test")
    assert r.method == "GET"


@pytest.mark.parametrize(
    "name", ["unmatched", "UNMATCHED", "Unmatched", "bad-name", "bad name", ""]
)
def test_route_spec_invalid_name(name: str) -> None:
    with pytest.raises(ValidationError):
        RouteSpec(name=name, method="GET", path="/test")


def test_route_spec_invalid_method() -> None:
    with pytest.raises(ValidationError):
        RouteSpec(name="test", method="INVALID", path="/test")


@pytest.mark.parametrize(
    "path",
    [
        "no_leading_slash",
        "/empty//segment",
        "/bad/{id}suffix",
        "/bad/prefix{id}",
        "/bad/{}",
    ],
)
def test_route_spec_invalid_path(path: str) -> None:
    with pytest.raises(ValidationError):
        RouteSpec(name="test", method="GET", path=path)


def test_manifest_rejects_duplicate_method_path() -> None:
    with pytest.raises(ValidationError, match="duplicate route"):
        CompanyManifest(
            name="test",
            language="python",
            run="python -m uvicorn app:app --port {port}",
            source_dirs=["app"],
            routes=[
                RouteSpec(name="first", method="GET", path="/items"),
                RouteSpec(name="second", method="GET", path="/items/"),
            ],
        )


def test_route_matcher_basic_matching() -> None:
    routes = [
        RouteSpec(name="health", method="GET", path="/health"),
        RouteSpec(name="hello", method="GET", path="/hello"),
    ]
    matcher = RouteMatcher(routes)
    assert matcher.match("GET", "/health") == "health"
    assert matcher.match("GET", "/hello") == "hello"
    assert matcher.match("POST", "/hello") == "unmatched"
    assert matcher.match("GET", "/unknown") == "unmatched"


def test_route_matcher_trailing_slash_tolerance() -> None:
    routes = [
        RouteSpec(name="hello", method="GET", path="/hello"),
        RouteSpec(name="root", method="GET", path="/"),
    ]
    matcher = RouteMatcher(routes)
    assert matcher.match("GET", "/hello") == "hello"
    assert matcher.match("GET", "/hello/") == "hello"
    assert matcher.match("GET", "/") == "root"
    assert matcher.match("GET", "") == "root"


def test_route_matcher_query_and_fragment_tolerance() -> None:
    routes = [RouteSpec(name="search", method="GET", path="/search")]
    matcher = RouteMatcher(routes)
    assert matcher.match("GET", "/search?q=test") == "search"
    assert matcher.match("GET", "/search#section") == "search"


def test_route_matcher_params() -> None:
    routes = [
        RouteSpec(name="user_detail", method="GET", path="/users/{id}"),
        RouteSpec(
            name="user_item", method="GET", path="/users/{user_id}/items/{item_id}"
        ),
    ]
    matcher = RouteMatcher(routes)
    assert matcher.match("GET", "/users/123") == "user_detail"
    assert matcher.match("GET", "/users/alice/items/456") == "user_item"
    assert matcher.match("GET", "/users") == "unmatched"
    assert matcher.match("GET", "/users/alice/items") == "unmatched"


def test_route_matcher_precedence_literal_beats_param() -> None:
    routes = [
        RouteSpec(name="by_param", method="GET", path="/users/{id}"),
        RouteSpec(name="by_literal", method="GET", path="/users/me"),
    ]
    matcher = RouteMatcher(routes)
    assert matcher.match("GET", "/users/me") == "by_literal"
    assert matcher.match("GET", "/users/other") == "by_param"


def test_route_matcher_precedence_exact_method_beats_wildcard() -> None:
    routes = [
        RouteSpec(name="any_method", method="*", path="/items"),
        RouteSpec(name="get_method", method="GET", path="/items"),
    ]
    matcher = RouteMatcher(routes)
    assert matcher.match("GET", "/items") == "get_method"
    assert matcher.match("POST", "/items") == "any_method"
    assert matcher.match("DELETE", "/items") == "any_method"


def test_route_matcher_precedence_multi_segment() -> None:
    routes = [
        RouteSpec(name="two_params", method="GET", path="/a/{b}/{c}"),
        RouteSpec(name="literal_first", method="GET", path="/a/fixed/{c}"),
        RouteSpec(name="literal_second", method="GET", path="/a/{b}/fixed"),
    ]
    matcher = RouteMatcher(routes)
    assert matcher.match("GET", "/a/fixed/test") == "literal_first"
    assert matcher.match("GET", "/a/test/fixed") == "literal_second"
    assert matcher.match("GET", "/a/random/other") == "two_params"
