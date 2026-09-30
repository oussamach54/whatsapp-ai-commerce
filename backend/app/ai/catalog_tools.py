"""The entire model-accessible catalog tool surface."""
from app.ai.catalog_schemas import SearchProducts, GetProducts, strict_schema
from app.services.catalog_service import CatalogError

TOOL_MODELS = {"search_products": SearchProducts, "get_products": GetProducts}
TOOLS = [{"type": "function", "name": name, "strict": True,
          "description": ("Search active catalog products. Hard filters match the same variant. Null attributes are unknown."
                          if name == "search_products" else
                          "Read current active products/variants INCLUDING zero stock. Optional size/color filters run before the six-variant limit. "
                          "For attribute changes use the parent product ID, not the previous variant ID. "
                          "Unknown product IDs return not_found; a found product with no variants and has_more_variants=false means no matching active variant."),
          "parameters": strict_schema(model)} for name, model in TOOL_MODELS.items()]


class CatalogTools:
    def __init__(self, service):
        self.service = service
        self.candidates = {}
        self.last_result = None
        self.constraints = None
        self.detail_query = None

    def execute(self, name, arguments):
        if name not in TOOL_MODELS:
            raise CatalogError("unauthorized_tool")
        if not isinstance(arguments, str) or len(arguments) > 3000:
            raise CatalogError("invalid_arguments")
        args = TOOL_MODELS[name].model_validate_json(arguments)
        if name == "search_products":
            self.constraints = args
            self.detail_query = None
            result = self.service.search(args)
        else:
            self.detail_query = args
            # Keep preceding query constraints for validation. Detail lookups
            # supersede stock filtering, not the other customer constraints.
            result = self.service.get(args)
        # Drop whole records/variants rather than truncating JSON. Never retain hidden candidates.
        while len(result.model_dump_json()) > 8000:
            if len(result.products) > 1:
                result.products.pop()
                result.has_more = True
            elif result.products and len(result.products[0].variants) > 1:
                result.products[0].variants.pop()
                result.products[0].has_more_variants = True
            else:
                raise CatalogError("result_too_large")
        self.last_result = result
        self.candidates = {p.id: p for p in result.products}
        return result.model_dump_json()
