from fastapi.routing import APIRoute


class CamelCaseAPIRoute(APIRoute):
    """Serialize response models with camelCase aliases to match prism_fe types."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("response_model_by_alias", True)
        super().__init__(*args, **kwargs)
