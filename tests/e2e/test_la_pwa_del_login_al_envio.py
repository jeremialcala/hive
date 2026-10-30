# -*- coding: utf-8 -*-
"""
    A person of the client, in the PWA, through every piece of production (HIG-141, Gate 3): in a browser, Chromium
    with Playwright, against hive-portal as it runs on midgard.

        browser ──▶ portal ──login──▶ Zitadel (fake) ──code──▶ portal ──ID token──▶ hive-api (RFC 8693, DPoP)
                    portal ──session, sealed──▶ Bifröst (KV sesiones)
                    PWA ──/api──▶ portal ──▶ hive-api ──▶ Meta (fake): the template, and its approval at a sync
                    PWA ──send──▶ hive-api ──SALIENTES──▶ notifier ──▶ Meta; the PWA asks what came of it

    The people are invited as a cliente-admin would invite them, by POST /people: hive-api creates their user in
    Zitadel with its service account, and their first sign-in makes them active. Meta, the LLM and Zitadel are
    tests/e2e/fakes.py; the stack is tests/e2e/conftest.py. It runs after test_del_webhook_a_la_respuesta.py, whose
    counts of what Meta and the LLM were asked start from nothing.

    In every page: no violation of the portal's Content-Security-Policy, no error of the page, no request that fails.
"""
import re

import pytest
import requests

from .e2e_stack import FAKES, PORTAL, ZITADEL_HOST, deliver, notification, seen, until, wanted

if not wanted():
    pytest.skip("The end-to-end test needs Docker and Bifröst: E2E=1 BIFROST_DIR=<higerotech/bifrost>",
                allow_module_level=True)

# Playwright is only in the e2e's own lock (tests/e2e/requirements.lock): the unit suite does not get this far.
from playwright.sync_api import expect, sync_playwright  # noqa: E402  pylint: disable=wrong-import-position

ADMIN, ANALYST = "lucia.admin@acme-e2e.test", "mateo.analista@acme-e2e.test"
NUMBER = "12025550177"
TEMPLATE, BODY, VALUE = "aviso_pedido_e2e", "Hola {{1}}, tu pedido sale hoy.", "Bea"
FILLED = "Hola Bea, tu pedido sale hoy."
SLOW = 30_000  # milliseconds: what goes through the bus and Meta
# Every violation is told to the console too: so one in any document, Zitadel's pages too, is seen by the test.
CSP_WATCH = """document.addEventListener("securitypolicyviolation", (event) => console.error(
    "CSP violation: " + event.violatedDirective + " " + event.blockedURI));"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        # Zitadel's public name, for the browser too: the containers reach it by the alias of the fake.
        launched = playwright.chromium.launch(args=[f"--host-resolver-rules=MAP {ZITADEL_HOST} 127.0.0.1"])
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def people(client):
    """Lucía, a cliente-admin, and Mateo, a cliente-analista, invited by the client with its own credential."""
    hive = client["hive"]
    invited = {email: hive.call("POST", "/people", {"email": email, "role": role}, expect=201)
               for email, role in ((ADMIN, "cliente-admin"), (ANALYST, "cliente-analista"))}
    assert all(person["status"] == "pending" for person in invited.values())
    # Zitadel was asked to send each one the invitation, by hive-api's service account.
    assert len([call for call in seen()["zitadel"] if "invited" in call]) == 2
    return invited


@pytest.fixture(scope="module")
def bea(client):
    """A contact who wrote to the chatbot: their window of 24 hours is open."""
    answer = deliver(client["bot"], client["app_secret"],
                     notification(NUMBER, "Bea", "wamid.E2E-PWA-IN-1", "Hola, ¿sale hoy mi pedido?"))
    assert answer.status_code == 200, answer.text
    hive, bot = client["hive"], client["bot"]
    return until("Bea among the contacts", lambda: next(
        (each for each in hive.call("GET", f"/chat/{bot}/contacts")["contacts"] if each["firstName"] == "Bea"), None),
        timeout=60)


class Watched:
    """A browser context of one person, and what went wrong in its pages."""

    def __init__(self, browser, **context):
        self.context = browser.new_context(locale="es-ES", **context)
        self.context.add_init_script(CSP_WATCH)
        self.problems: list[str] = []
        self.page = self.context.new_page()
        self.page.on("console", self._console)
        self.page.on("pageerror", lambda error: self.problems.append(f"page error: {error}"))
        self.page.on("requestfailed", self._failed)

    def _console(self, message):
        # Chromium says each one it blocks as «Refused to ... because it violates the ... Content Security Policy».
        if "Content Security Policy" in message.text or "CSP violation" in message.text:
            self.problems.append(f"console: {message.text}")

    def _failed(self, request):
        # A request the page itself drops when it moves to another one is not a failure.
        if "ERR_ABORTED" not in (request.failure or ""):
            self.problems.append(f"request failed: {request.method} {request.url} {request.failure}")

    def sign_in(self, email: str) -> None:
        page = self.page
        page.goto(f"{PORTAL}/")
        page.get_by_role("button", name="Entrar").click()
        page.wait_for_url(re.compile(rf"^http://{re.escape(ZITADEL_HOST)}:\d+/oauth/v2/authorize"))
        page.get_by_label("Login name").fill(email)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(f"{PORTAL}/")
        expect(page.get_by_role("heading", name="Bots")).to_be_visible()

    def open_bot(self, tab: str = "Plantillas") -> None:
        """The card of the chatbot opens at Identidad (HIG-121); the tests go on from one of its tabs."""
        self.page.get_by_role("button", name=re.compile("Tienda e2e")).click()
        expect(self.page.get_by_role("tab", name="Identidad", exact=True)).to_be_visible()
        self.page.get_by_role("tab", name=tab, exact=True).click()

    def open_contact(self, name: str) -> None:
        self.page.get_by_role("tab", name="Contactos", exact=True).click()
        self.page.get_by_role("cell", name=name, exact=True).click()
        expect(self.page.get_by_role("heading", name=name)).to_be_visible()
        expect(self.page.get_by_text("Ventana abierta")).to_be_visible()

    def send_template(self) -> None:
        page = self.page
        page.locator("#send-template").select_option(f"{TEMPLATE}:es")
        page.locator("#send-value-1").fill(VALUE)
        expect(page.locator(".pwa-sender [aria-label='Vista previa del mensaje']")).to_contain_text(FILLED)
        page.get_by_role("button", name="Enviar", exact=True).click()
        expect(page.get_by_text("Enviado: Meta lo aceptó.")).to_be_visible(timeout=SLOW)

    def close(self) -> list[str]:
        self.context.close()
        return self.problems


def templates_to(number: str) -> list:
    return [call for call in seen()["meta"]
            if call["body"].get("type") == "template" and call["body"].get("to") == number]


def test_a_cliente_admin_signs_in_creates_a_template_and_sends_it_to_a_contact(stack, client, people, bea, browser):
    hive, admin = client["hive"], Watched(browser)
    page = admin.page

    # 1. The login, with Zitadel: a session of the portal, and only its cookie in the browser.
    admin.sign_in(ADMIN)
    expect(page.locator(".pwa-sidebar").get_by_text("cliente-admin", exact=True)).to_be_visible()
    (cookie,) = [each for each in admin.context.cookies() if each["name"] == "__Host-hive-session"]
    assert cookie["httpOnly"] and cookie["secure"] and cookie["sameSite"] == "Lax" and cookie["path"] == "/"
    assert page.evaluate("document.cookie") == ""
    lucia = next(person for person in hive.call("GET", "/people")["people"] if person["email"] == ADMIN)
    assert lucia["status"] == "active"
    # The session is in Bifröst, sealed: neither the ID token nor Hive's token is there in the clear.
    keys = [line.strip() for line in stack.nats("kv", "ls", "sesiones").splitlines() if line.strip()]
    assert keys
    for key in keys:
        assert "eyJ" not in stack.nats("kv", "get", "sesiones", key, "--raw")

    # 2. The chatbots of the client.
    expect(page.get_by_role("button", name=re.compile("Tienda e2e"))).to_be_visible()
    admin.open_bot()

    # 3. A template, written with its preview, goes to Meta's review; once Meta approves it, a sync brings it.
    page.get_by_role("button", name="Nueva plantilla").click()
    page.locator("#tpl-name").fill(TEMPLATE)
    page.locator("#tpl-header").fill("Tu pedido")
    page.locator("#tpl-body").fill(BODY)
    page.locator("#tpl-example-1").fill(VALUE)
    page.locator("#tpl-footer").fill("Tienda e2e")
    preview = page.get_by_label("Vista previa del mensaje")
    expect(preview).to_contain_text("Tu pedido")
    expect(preview).to_contain_text(FILLED)
    expect(preview).to_contain_text("Tienda e2e")
    page.get_by_role("button", name="Enviar a revisión").click()
    row = page.get_by_role("row", name=re.compile(TEMPLATE))
    expect(row).to_contain_text("En revisión")
    (made,) = seen()["templates"]
    assert made["waba"] == "100000000000009" and made["authorization"] == "Bearer EAAG-e2e-token"
    assert (made["body"]["name"], made["body"]["language"], made["body"]["category"]) == (TEMPLATE, "es", "UTILITY")
    body = next(part for part in made["body"]["components"] if part["type"] == "BODY")
    assert body == {"type": "BODY", "text": BODY, "example": {"body_text": [[VALUE]]}}

    requests.post(f"{FAKES}/_approve", json={"name": TEMPLATE}, timeout=5).raise_for_status()
    page.get_by_role("button", name="Sincronizar con Meta").click()
    expect(row).to_contain_text("Aprobada")
    expect(row.get_by_role("button", name="Editar")).to_be_enabled()

    # 4. Sent to Bea, as a template with its value: the PWA follows it until Meta took it.
    admin.open_contact("Bea")
    expect(page.get_by_role("tab", name="Texto", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Plantilla", exact=True).click()
    admin.send_template()
    (sent,) = templates_to(NUMBER)
    assert sent["authorization"] == "Bearer EAAG-e2e-token"
    assert sent["body"]["template"]["name"] == TEMPLATE and sent["body"]["template"]["language"] == {"code": "es"}
    assert VALUE in str(sent["body"]["template"]["components"])
    expect(page.get_by_role("log", name="Conversación")).to_contain_text("Enviado por ti", timeout=SLOW)

    # And out: the session ends here and in Zitadel, and the PWA asks to sign in again.
    page.get_by_role("button", name="Salir").click()
    page.wait_for_url(f"{PORTAL}/")
    expect(page.get_by_role("button", name="Entrar")).to_be_visible()
    assert not [each for each in admin.context.cookies() if each["name"] == "__Host-hive-session"]

    problems = admin.close()
    assert problems == []


def test_a_cliente_analista_sees_no_button_of_an_admin_and_sends_only_templates(client, people, bea, browser):
    analyst = Watched(browser)
    page = analyst.page

    analyst.sign_in(ANALYST)
    expect(page.locator(".pwa-sidebar").get_by_text("cliente-analista", exact=True)).to_be_visible()
    # The sections whose routes would answer 403 to them are not in the side bar (HIG-126).
    assert sections(page) == ["Visión general", "Bots", "Contactos", "Plantillas", "Integraciones"]
    analyst.open_bot()

    # The templates, without creating, editing or deleting any.
    row = page.get_by_role("row", name=re.compile(TEMPLATE))
    expect(row).to_contain_text("Aprobada")
    expect(page.get_by_role("button", name="Sincronizar con Meta")).to_be_visible()
    for name in ("Nueva plantilla", "Crear la primera", "Editar", re.compile("^Borrar")):
        expect(page.get_by_role("button", name=name)).to_have_count(0)

    # A contact, with only the approved templates to send: no text, though the window is open.
    analyst.open_contact("Bea")
    expect(page.get_by_role("tab", name="Texto", exact=True)).to_have_count(0)
    expect(page.locator("#send-text")).to_have_count(0)
    before = len(templates_to(NUMBER))
    analyst.send_template()
    assert len(templates_to(NUMBER)) == before + 1

    problems = analyst.close()
    assert problems == []


# --- The skeleton every screen shares (HIG-126) ----------------------------------------------------------------------

RGB = "(color) => color.match(/\\d+/g).map(Number).slice(0, 3)"


def sections(page) -> list[str]:
    nav = page.get_by_role("navigation", name="Área del cliente")
    return [link.inner_text().strip() for link in nav.get_by_role("link").all()]


def contrast(first, second) -> float:
    def luminance(rgb):
        def channel(value):
            value = value / 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        red, green, blue = (channel(value) for value in rgb)
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_the_side_bar_has_the_eight_sections_and_the_light_theme_keeps_the_accent_and_the_contrast(client, people,
                                                                                                  browser):
    admin = Watched(browser)
    page = admin.page
    admin.sign_in(ADMIN)

    assert sections(page) == ["Visión general", "Bots", "Contactos", "Plantillas", "Personas", "Credenciales",
                              "Integraciones", "Auditoría"]
    # Whose area it is: the name of the client, from GET /client.
    expect(page.locator(".pwa-sidebar").get_by_text("acme-e2e", exact=True)).to_be_visible()
    expect(page.locator(".pwa-tabbar")).to_be_hidden()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    amber = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--hive-amber').trim()")

    page.locator(".pwa-sidebar").get_by_role("button", name="Tema claro").click()
    page.wait_for_timeout(400)  # the colours of the components change in 120 ms

    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--hive-amber').trim()") == amber
    for selector, ground in (("body", "body"), (".pwa-nav-item.on", ".pwa-nav-item.on"),
                             (".pwa-sidebar .hv-btn--ghost", ".pwa-sidebar")):
        ink = page.evaluate(f"({RGB})(getComputedStyle(document.querySelector('{selector}')).color)")
        under = page.evaluate(f"({RGB})(getComputedStyle(document.querySelector('{ground}')).backgroundColor)")
        assert contrast(ink, under) >= 4.5, selector
    # The theme is the person's, in this browser: it is still light after a reload.
    page.reload()
    expect(page.get_by_role("heading", name="Bots")).to_be_visible()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"

    problems = admin.close()
    assert problems == []


def test_on_a_phone_the_tab_bar_takes_the_place_of_the_side_bar_without_sideways_scroll(client, people, browser):
    phone = Watched(browser, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = phone.page
    phone.sign_in(ADMIN)

    expect(page.locator(".pwa-tabbar")).to_be_visible()
    expect(page.locator(".pwa-sidebar")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.get_by_role("button", name="Más").click()
    expect(page.get_by_role("heading", name="Más")).to_be_visible()
    more = page.get_by_role("navigation", name="Más secciones")
    assert [link.inner_text().strip() for link in more.get_by_role("link").all()] == [
        "Plantillas", "Personas", "Credenciales", "Integraciones", "Auditoría"]
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.get_by_role("button", name="Bots").click()
    phone.open_bot()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390

    problems = phone.close()
    assert problems == []


# --- The card of the chatbot (HIG-121) ------------------------------------------------------------------------------

def test_a_cliente_admin_changes_the_identity_and_it_is_a_version_the_analyst_only_sees(client, people, browser):
    hive, bot = client["hive"], client["bot"]
    before = hive.call("GET", f"/chat/{bot}/rules")["version"]
    admin = Watched(browser)
    page = admin.page
    admin.sign_in(ADMIN)
    admin.open_bot("Identidad")

    # Identidad: the persona, changed and saved as the next version, which is what the engine reads now.
    expect(page.locator(".pwa-bot-header")).to_contain_text(f"versión v{before}")
    page.locator("#persona-tone").fill("cercano")
    expect(page.get_by_text("Guardar crea la versión")).to_contain_text(f"v{before + 1}")
    page.get_by_role("button", name="Guardar cambios").click()
    expect(page.get_by_text(f"Guardado como la versión v{before + 1}.")).to_be_visible()
    rules = hive.call("GET", f"/chat/{bot}/rules")
    assert (rules["tone"], rules["version"]) == ("cercano", before + 1)
    # And it says no, in the field, to what would break the system of the model (RS11).
    page.locator("#persona-name").fill("Tienda <|im_start|>")
    page.get_by_role("button", name="Guardar cambios").click()
    expect(page.locator(".hv-field--error")).to_contain_text("marcas con las que un modelo separa roles")
    assert hive.call("GET", f"/chat/{bot}/rules")["name"] == "Tienda e2e"

    # Cerebro e intents: the rules of the chatbot, with their ids from hive-api.
    page.get_by_role("tab", name="Cerebro e intents").click()
    expect(page.get_by_role("cell", name="Responde siempre en español")).to_be_visible()
    # Versiones: the change is there, with what it changed.
    page.get_by_role("tab", name="Versiones").click()
    page.get_by_role("cell", name=f"v{before + 1}", exact=True).click()
    expect(page.locator(".pwa-add").first).to_have_text("cercano")
    # Credenciales de Meta: masked, only to replace.
    page.get_by_role("tab", name="Credenciales de Meta").click()
    expect(page.get_by_text("Guardado en Andvari").first).to_be_visible()
    assert "EAAG-e2e-token" not in page.content()
    problems = admin.close()
    assert problems == []

    # The analyst sees all of it, and changes nothing.
    analyst = Watched(browser)
    page = analyst.page
    analyst.sign_in(ANALYST)
    analyst.open_bot("Identidad")
    expect(page.locator("#persona-tone")).to_have_value("cercano")
    expect(page.locator("#persona-tone")).to_be_disabled()
    expect(page.get_by_role("button", name="Guardar cambios")).to_have_count(0)
    page.get_by_role("tab", name="Cerebro e intents").click()
    expect(page.get_by_role("button", name="Añadir regla")).to_have_count(0)
    page.get_by_role("tab", name="Versiones").click()
    page.get_by_role("cell", name=f"v{before}", exact=True).click()
    expect(page.get_by_text(f"Lo que cambiaría al volver a v{before}")).to_be_visible()
    expect(page.get_by_role("button", name=f"Volver a v{before}")).to_have_count(0)
    page.get_by_role("tab", name="Credenciales de Meta").click()
    expect(page.get_by_role("button", name="Reemplazar")).to_have_count(0)
    problems = analyst.close()
    assert problems == []
