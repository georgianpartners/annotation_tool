import logging
import os

from flask import (
    Flask, render_template, g
)

from alchemy.db.model import db

from .auth import login_required

from alchemy.ar.data import fetch_tasks_for_user_from_db


def _setup_logging(config):
    from google.cloud import logging as glog
    from google.cloud.logging.handlers import CloudLoggingHandler, setup_logging

    client = glog.Client()

    handler = CloudLoggingHandler(client, name=config['ANNOTATION_SERVER_LOGGER'])
    logging.getLogger().setLevel(logging.INFO)
    setup_logging(handler)


def create_app():
    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_envvar('ALCHEMY_CONFIG')
    if app.config['USE_CLOUD_LOGGING']:
        _setup_logging(app.config)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    db.init_app(app)

    @app.route("/ok")
    def hello():
        return "ok"

    @app.route("/")
    @login_required
    def index():
        username = g.user["username"]
        task_id_and_name_pairs = fetch_tasks_for_user_from_db(db.session, username)
        return render_template("index.html", tasks=task_id_and_name_pairs)

    @app.route("/secret")
    @login_required
    def secret():
        return render_template("secret.html")

    from . import auth

    app.register_blueprint(auth.bp)

    from . import tasks

    app.register_blueprint(tasks.bp)

    from . import labels

    app.register_blueprint(labels.bp)

    return app


"""
env FLASK_APP=annotation_server FLASK_ENV=development flask init-db

env FLASK_APP=annotation_server FLASK_ENV=development flask run
"""
