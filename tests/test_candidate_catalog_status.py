import json
from pathlib import Path


def test_manual_trial_can_mark_candidates_without_inventing_measurements():
    from iptv_manager.catalog import catalog_content_hash, validate_channel_catalog
    document = json.loads(Path('schema/fixtures/channels-v1.json').read_text(encoding='utf-8'))
    for channel in document['channels']:
        for source in channel['sources']:
            source.update(measurementStatus='candidateUnverified', score=0, resolution=None,
                          videoCodec=None, frameRate=None, lastChecked=None, lastSuccess=None, availability=None)
    document['contentHash'] = catalog_content_hash(document['channels'])
    validate_channel_catalog(document)
