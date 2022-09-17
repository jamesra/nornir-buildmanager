import logging


def ValidateAttributesAreStrings(Element, logger=None):
    # Make sure each attribute is a string
    for k, v in enumerate(Element.attrib):
        assert isinstance(v, str)
        if v is None or not isinstance(v, str):
            if logger is None:
                logger = logging.getLogger(__name__ + '.' + 'ValidateAttributesAreStrings')
            logger.warning("Attribute is not a string")
            Element.attrib[k] = str(v)
