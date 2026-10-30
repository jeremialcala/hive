# -*- coding: utf-8 -*-
"""
El motor usa el código que comparte con el gateway (`shared`, ADR-0023 del gateway) tal como lo usará el
clasificador: lee un MensajeIdentificado con el modelo del contrato y sabe sobre qué sujeto llega.
"""
from uuid import uuid4

from shared.contract import IdentifiedMessage, identified_subject
from shared.workers import Worker


def test_el_motor_lee_un_mensaje_identificado_con_el_contrato_compartido():
    bot, cliente, contacto = uuid4(), uuid4(), uuid4()
    evento = {"schemaVersion": 2, "botId": str(bot), "clientId": str(cliente), "messageId": "wamid.A",
              "message": {"messages": [{"id": "wamid.A", "type": "text", "text": {"body": "hola"}}]},
              "channel": "whatsapp", "receivedAt": "2026-10-20T09:00:00+00:00", "orderKey": f"{bot}.{contacto}",
              "contact": {"uuid": str(contacto), "name": "Ana", "waId": "12025550143"}}

    leido = IdentifiedMessage.model_validate(evento)

    assert (leido.botId, leido.clientId, leido.contact.uuid) == (bot, cliente, contacto)
    assert identified_subject(3, bot, contacto) == f"gateway.message.3.{bot}.{contacto}"


def test_los_workers_del_motor_son_los_que_valida_la_api_del_gateway():
    assert [worker.value for worker in Worker] == ["classifier", "coordinator", "link", "responder", "evaluator",
                                                   "notifier"]
