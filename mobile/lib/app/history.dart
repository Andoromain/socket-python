// Ouverture de l'historique chiffré, transposé de app.open_history (version PC).

import '../store/store.dart';
import 'secrets.dart';

const String historyKeyName = 'history_key';

/// Historique chiffré de l'appareil ; renvoie (historique, avertissement ou null).
///
/// Si la clé est perdue (données restaurées sur un autre téléphone, Keystore réinitialisé),
/// l'ancien historique ne peut plus être lu : il est mis de côté (renommé, jamais effacé)
/// et un nouveau commence.
Future<(Store, String?)> openHistory(String directory, SecretStore secrets) async {
  final path = '$directory/history.sqlite3';
  final (key, _) = await loadOrCreateSecret(secrets, historyKeyName);
  try {
    return (Store(path, key), null);
  } on HistoryLockedException {
    final old = setAside(path);
    final store = Store(path, key);
    if (old == null) return (store, null);
    final name = old.substring(old.lastIndexOf('/') + 1);
    return (
      store,
      "L'historique des conversations de cet appareil ne peut plus être déchiffré (clé "
          'perdue). Un nouvel historique commence ; l’ancien est conservé, chiffré, dans « $name ».',
    );
  }
}
