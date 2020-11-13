from dataclasses import dataclass, fields
from typing import List

from alchemy.data.request.base_request import (
    ValidRequest,
    InvalidRequest,
    validate_request_data_common,
)


@dataclass
class TaskCreateRequest(ValidRequest):
    name: str
    entity_type: str
    annotators: List
    labels: List
    data_files: List

    @classmethod
    def from_dict(cls, dict_data):
        invalid_req = InvalidRequest()

        cls._validate_request_data(dict_data, invalid_req)

        if invalid_req.has_errors():
            return invalid_req

        return cls(**dict_data)

    @classmethod
    def _validate_request_data(cls, dict_data, invalid_req):
        data_fields = fields(cls)
        validate_request_data_common(
            fields=data_fields, dict_data=dict_data, invalid_req=invalid_req
        )
        for field_name in ["annotators", "labels", "data_files"]:
            if field_name in dict_data:
                if (
                    dict_data[field_name] is not None
                    and len(dict_data[field_name]) == 0
                ):
                    invalid_req.add_error(
                        parameter="dict_data", message=f"Field {field_name} is empty."
                    )
                elif dict_data[field_name] is None:
                    invalid_req.add_error(
                        parameter="dict_data", message=f"Field {field_name} is None."
                    )