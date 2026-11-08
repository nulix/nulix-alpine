#!/usr/bin/env python3
"""Generate SPDX package evidence from the completed Alpine rootfs, including custom kernel."""
import hashlib,json,os,re,sys
from datetime import datetime,timezone
from pathlib import Path
root=Path(sys.argv[1]);kernel=sys.argv[2]
packages=[]
for record in (root/'lib/apk/db/installed').read_text().split('\n\n'):
 fields=dict(line.split(':',1) for line in record.splitlines() if len(line)>1 and line[1]==':')
 if fields.get('P') and fields.get('V'):
  packages.append({'SPDXID':'SPDXRef-'+fields['P'],'name':fields['P'],'versionInfo':fields['V'],'downloadLocation':'NOASSERTION','filesAnalyzed':False,'licenseConcluded':'NOASSERTION','licenseDeclared':fields.get('L','NOASSERTION'),'copyrightText':'NOASSERTION'})
agent=(root/'usr/libexec/nulix-agent.py').read_text()
version=re.search(r'^VERSION\s*=\s*[\'\"]([^\'\"]+)',agent,re.M)[1]
for name,value,license in [('linux',kernel,'GPL-2.0-only'),('nulix-agent',version,'NOASSERTION')]:
 packages.append({'SPDXID':'SPDXRef-'+name,'name':name,'versionInfo':value,'downloadLocation':'NOASSERTION','filesAnalyzed':False,'licenseConcluded':'NOASSERTION','licenseDeclared':license,'copyrightText':'NOASSERTION',**({'primaryPackagePurpose':'OPERATING-SYSTEM'} if name=='linux' else {})})
packages.sort(key=lambda p:p['name'])
digest=hashlib.sha256(json.dumps(packages,sort_keys=True).encode()).hexdigest()
sbom={'spdxVersion':'SPDX-2.3','dataLicense':'CC0-1.0','SPDXID':'SPDXRef-DOCUMENT','name':'NULIX OS '+os.environ.get('DISTRO_VERSION','unknown'),'documentNamespace':'https://nulix.io/sbom/'+digest,'creationInfo':{'created':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'creators':['Tool: nulix-build-sbom']},'packages':packages,'relationships':[{'spdxElementId':'SPDXRef-DOCUMENT','relationshipType':'DESCRIBES','relatedSpdxElement':p['SPDXID']} for p in packages]}
output=root/'usr/share/nulix/sbom.spdx.json';output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(sbom,separators=(',',':'))+'\n')
