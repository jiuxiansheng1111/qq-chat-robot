from app.config import Settings
from app.services.onebot_routing import onebot_route, set_current_onebot_self_id


def test_secondary_onebot_route_is_selected_by_event_self_id():
    settings = Settings(
        _env_file=None,
        onebot_api_base="http://127.0.0.1:3000",
        onebot_access_token="primary-token",
        onebot_webhook_token="primary-webhook",
        onebot_self_id="111111",
        onebot_api_base_2="http://127.0.0.1:3001",
        onebot_access_token_2="secondary-token",
        onebot_webhook_token_2="secondary-webhook",
        onebot_self_id_2="3503565007",
    )

    route = onebot_route(settings, "3503565007")

    assert route.self_id == "3503565007"
    assert route.api_base == "http://127.0.0.1:3001"
    assert route.access_token == "secondary-token"
    assert route.webhook_token == "secondary-webhook"


def test_secondary_route_can_reuse_primary_base_and_webhook_token():
    settings = Settings(
        _env_file=None,
        onebot_api_base="http://127.0.0.1:3000",
        onebot_access_token="primary-token",
        onebot_webhook_token="shared-webhook",
        onebot_self_id="111111",
        onebot_self_id_2="3503565007",
        onebot_access_token_2="secondary-token",
    )

    route = onebot_route(settings, "3503565007")

    assert route.api_base == "http://127.0.0.1:3000"
    assert route.access_token == "secondary-token"
    assert route.webhook_token == "shared-webhook"


def test_current_onebot_context_routes_background_calls():
    settings = Settings(
        _env_file=None,
        onebot_api_base="http://127.0.0.1:3000",
        onebot_access_token="primary-token",
        onebot_self_id="111111",
        onebot_api_base_2="http://127.0.0.1:3001",
        onebot_access_token_2="secondary-token",
        onebot_self_id_2="3503565007",
    )

    set_current_onebot_self_id("3503565007")
    route = onebot_route(settings)

    assert route.self_id == "3503565007"
    assert route.api_base == "http://127.0.0.1:3001"
    assert route.access_token == "secondary-token"
