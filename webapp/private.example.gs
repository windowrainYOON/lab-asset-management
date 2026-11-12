/* Copy to private/Private.gs (gitignored) and fill in. tools/bundle.js puts it first in paste/Code.gs.
 * ids: Drive ids of the lab's Google Sheets (used when the settings JSON still points at an old .xlsx)
 *      and of "Asset Manager 설정.json" (overlay). Script property CONFIG_FILE_ID wins over overlay.
 * pin: the first lab password as a hash, until someone sets one in the desktop app. Make it with
 *      python3 -c "import base64,hashlib,os,getpass; s=os.urandom(16); p=getpass.getpass();
 *      print(base64.b64encode(s).decode(), base64.b64encode(hashlib.pbkdf2_hmac('sha256', p.encode(), s, 2000)).decode())"
 */
var PRIVATE_ = {
  ids: {
    attendance: '', reservations: '', notices: '', order_requests: '', work: '', budget: '',
    overlay: ''
  },
  pin: { salt: '', hash: '', iterations: 2000, version: 1 }
};
