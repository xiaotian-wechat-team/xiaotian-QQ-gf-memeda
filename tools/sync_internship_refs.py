#!/usr/bin/env python3
"""Install only visually approved reference originals, with SHA256 verification.
Persona SSOT: data/persona_dossier.json. No collection/search or chat sending.
"""
import json,pathlib,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import build_scene_refs as b

def sync(manifest=None):
    manifest=manifest or json.loads((ROOT/'data/internship_scene_refs_manifest.json').read_text())
    scenes={s['id']:s for s in b.load_scenes()}
    con=b.ensure();result={'present':0,'added':0,'failed':[]}
    try:
        for item in manifest['images']:
            row=con.execute('SELECT path FROM images WHERE scene_id=? AND sha256=?',(item['scene_id'],item['sha256'])).fetchone()
            if row and pathlib.Path(row[0]).is_file():
                result['present']+=1;continue
            data,why=b.download_one(item['source_url'])
            if not data or b.sha256_bytes(data)!=item['sha256']:
                result['failed'].append({'scene_id':item['scene_id'],'reason':why if not data else 'source changed: hash mismatch'});continue
            _,why=b.save_image(con,scenes[item['scene_id']],item['source'],item['source_url'],data,item['query'],None)
            if why=='ok': result['added']+=1
            else: result['failed'].append({'scene_id':item['scene_id'],'reason':why})
            time.sleep(.5)
    finally:
        con.close()
    return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, help="alternate visually reviewed scene reference manifest")
    args=parser.parse_args()
    result=sync(json.loads(args.manifest.read_text()) if args.manifest else None)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(bool(result['failed']))
