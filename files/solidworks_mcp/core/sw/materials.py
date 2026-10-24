"""Materials: the call that assigns a material from a database.
The tools do the inputs, messages and measuring."""


def sw_set_material(doc, database_path: str, name: str) -> None:
    doc.SetMaterialPropertyName2("", database_path, name)
