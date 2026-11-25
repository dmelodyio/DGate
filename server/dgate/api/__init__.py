# -*- coding: utf-8 -*-
from . import admin, client


def register_blueprints(app):
    app.register_blueprint(client.bp, url_prefix='/api/client')
    app.register_blueprint(admin.bp, url_prefix='/api/admin')
