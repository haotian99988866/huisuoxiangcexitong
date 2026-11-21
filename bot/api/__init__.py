from .media_routes import media_blueprint


def register_blueprints(app):
    app.register_blueprint(media_blueprint)