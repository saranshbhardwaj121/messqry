from uuid import uuid4

import pytest
from pydantic import ValidationError

from messqry.api.schemas import PredictionRequest, PredictionResponse


def test_prediction_request_accepts_bounded_homogeneous_json_records():
    request = PredictionRequest(
        inputs=[
            {"amount": 12.5, "region": "north"},
            {"amount": 7, "region": "south"},
        ]
    )

    assert request.inputs[0] == {"amount": 12.5, "region": "north"}
    assert len(request.inputs) == 2


@pytest.mark.parametrize(
    "inputs",
    [
        [],
        [{}],
        [{"amount": 1}, {"region": "north"}],
        [{" ": "invalid"}],
        [{"x" * 129: 1}],
        [{f"column_{index}": index for index in range(257)}],
        [{"amount": 1}] * 1001,
    ],
)
def test_prediction_request_rejects_empty_or_unbounded_records(inputs):
    with pytest.raises(ValidationError):
        PredictionRequest(inputs=inputs)


def test_prediction_response_identifies_the_served_model_version():
    model_id = uuid4()
    response = PredictionResponse(
        model_id=model_id,
        environment="production",
        model_version=4,
        predictions=[0.91, {"label": "approved"}],
    )

    assert response.model_id == model_id
    assert response.model_version == 4
    assert response.predictions == [0.91, {"label": "approved"}]


def test_prediction_response_rejects_zero_version_or_empty_predictions():
    with pytest.raises(ValidationError):
        PredictionResponse(
            model_id=uuid4(), environment="production", model_version=0, predictions=[1]
        )
    with pytest.raises(ValidationError):
        PredictionResponse(
            model_id=uuid4(), environment="production", model_version=1, predictions=[]
        )
