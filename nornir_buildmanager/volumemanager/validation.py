import logging


def ValidateAttributesAreStrings(Element, logger=None):
    # Make sure each attribute is a string
    for k, v in Element.attrib.items():
        if v is None or not isinstance(v, str):
            if logger is None:
                logger = logging.getLogger(__name__ + '.' + 'ValidateAttributesAreStrings')
            logger.warning("Attribute value is not a string for key %s", k)
            Element.attrib[k] = '' if v is None else str(v)
