import logging

from flask import Blueprint, request, jsonify, redirect, render_template
from sqlalchemy.exc import DatabaseError
import logging

from flask import (
    Blueprint, request, jsonify,
    redirect)
from sqlalchemy.exc import DatabaseError

from bg.jobs import export_new_raw_data as _export_new_raw_data
from db.model import db, Model, ModelDeploymentConfig

bp = Blueprint('models', __name__, url_prefix='/models')

# TODO API auth


def get_request_data():
    """Returns a dict of request keys and values, from either json or form"""
    if len(request.form) > 0:
        return request.form
    else:
        return request.get_json()


@bp.route('export_new_raw_data', methods=['POST'])
def export_new_raw_data():
    data = get_request_data()

    model_id = int(data.get('model_id'))
    data_fname = data.get('data_fname')
    output_fname = data.get('output_fname')
    cutoff = float(data.get('cutoff'))

    resp = {
        'error': None,
        'message': 'Request not processed.'
    }

    try:
        assert data_fname is not None, f"Missing data_fname"
        assert output_fname is not None, f"Missing output_fname"
        assert 0.0 <= cutoff < 1.0, f"Invalid cutoff={cutoff}"

        output_fname = output_fname.replace(' ', '_')

        model = db.session.query(Model).filter_by(id=model_id).one_or_none()
        output_path = _export_new_raw_data(
            model, data_fname, output_fname, cutoff=cutoff)
        resp['message'] = f'Successfully created raw data: {output_path}'
    except Exception as e:
        resp['error'] = str(e)
        resp['message'] = f"Error: {resp['error']}"

    return jsonify(resp)


@bp.route('update_model_deployment_config', methods=['POST'])
def update_model_deployment_config():
    logging.error(request.form)
    approved_model_ids = set(request.form.getlist("approved_model_id"))
    selected_model_id_for_deployment = request.form.get("selected_model_id")

    label = request.form.get("label")

    models = db.session.query(Model).filter(Model.label == label).all()

    for model in models:
        threshold = request.form.get(str(model.id) + "_threshold", None)
        if threshold:
            threshold = float(threshold)
        model_deployment_config = db.session.query(ModelDeploymentConfig).\
            filter(ModelDeploymentConfig.model_id == model.id).one_or_none()
        is_approved = str(model.id) in approved_model_ids
        is_selected_for_deployment = str(model.id) == selected_model_id_for_deployment
        if model_deployment_config:
            model_deployment_config.is_approved = is_approved
            model_deployment_config.is_selected_for_deployment = is_selected_for_deployment
            model_deployment_config.threshold = threshold
        else:
            model_deployment_config = ModelDeploymentConfig(
                model_id=model.id,
                is_approved=is_approved,
                is_selected_for_deployment=is_selected_for_deployment,
                threshold=threshold
            )
        db.session.add(model_deployment_config)

    try:
        db.session.commit()
    except DatabaseError as e:
        db.session.rollback()
        logging.error(e)
        raise

    logging.info(f"Updated model deployment config for label {label}.")

    return redirect(request.referrer)


@bp.route('/', methods=['GET'])
def index():
    pass


@bp.route('show/<string:label>', methods=['GET'])
def show(label):
    models_per_label = {}
    deployment_configs_per_model = {}
    models = db.session.query(Model).filter_by(
        label=label).order_by(Model.created_at.desc()).limit(10).all()
    models_per_label[label] = models
    model_ids = [model.id for model in models]
    res = db.session.query(
        ModelDeploymentConfig.model_id,
        ModelDeploymentConfig.is_approved,
        ModelDeploymentConfig.is_selected_for_deployment,
        ModelDeploymentConfig.threshold). \
        filter(ModelDeploymentConfig.model_id.in_(model_ids)).all()
    for model_id, is_approved, is_selected_for_deployment, threshold in \
            res:
        deployment_configs_per_model[model_id] = {
            "is_approved": is_approved,
            "is_selected_for_deployment": is_selected_for_deployment,
            "threshold": threshold
        }

    logging.error(models_per_label)
    logging.error(deployment_configs_per_model)

    return render_template(
        'models/show.html',
        models_per_label=models_per_label,
        deployment_configs_per_model=deployment_configs_per_model,
        label=label
    )
