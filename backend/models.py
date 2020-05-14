from flask import (
    Blueprint, request, jsonify
)

from bg.jobs import export_new_raw_data as _export_new_raw_data
from db.model import db, Model

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

        model = db.session.query(Model).filter_by(id=model_id).one_or_none()
        output_path = _export_new_raw_data(
            model, data_fname, output_fname, cutoff=cutoff)
        resp['message'] = f'Successfully created raw data: {output_path}'
    except Exception as e:
        resp['error'] = str(e)
        resp['message'] = f"Error: {resp['error']}"

    return jsonify(resp)