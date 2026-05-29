import * as vscode from 'vscode';

const NEW_EXTENSION_ID = 'AgentOpsAccelerator.agentops-accelerator';
const STORAGE_KEY = 'agentops-toolkit.deprecation-prompt-shown';

const DEPRECATION_MESSAGE =
    'The "AgentOps Toolkit" extension has been renamed to "AgentOps Accelerator". ' +
    `Please install the new extension (${NEW_EXTENSION_ID}) and uninstall this one ` +
    'to continue receiving updates.';

const INSTALL_ACTION = 'Install new extension';
const MARKETPLACE_ACTION = 'Open Marketplace page';
const DISMISS_ACTION = 'Dismiss';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    // If the new extension is already installed, stay silent — nothing to migrate.
    if (vscode.extensions.getExtension(NEW_EXTENSION_ID)) {
        return;
    }

    // If we've already shown the prompt once, don't badger the user again.
    if (context.globalState.get<boolean>(STORAGE_KEY, false)) {
        return;
    }

    const choice = await vscode.window.showWarningMessage(
        DEPRECATION_MESSAGE,
        INSTALL_ACTION,
        MARKETPLACE_ACTION,
        DISMISS_ACTION,
    );

    // Only persist the "already prompted" flag when the prompt was actually
    // resolved (success, opened the marketplace, or explicit dismissal).
    // If the user wanted to install but the command failed (offline, transient
    // 5xx, auth cancel), leave the flag unset so we re-prompt next session.
    let promptResolved = false;

    if (choice === INSTALL_ACTION) {
        try {
            await vscode.commands.executeCommand(
                'workbench.extensions.installExtension',
                NEW_EXTENSION_ID,
            );
            promptResolved = true;
        } catch (err) {
            await vscode.window.showErrorMessage(
                `Couldn't install ${NEW_EXTENSION_ID}. We'll try again next time. (${(err as Error).message})`,
            );
            // Leave promptResolved = false so the user gets another chance next launch.
        }
    } else if (choice === MARKETPLACE_ACTION) {
        // openExternal resolves to false when no handler accepts the URI
        // (e.g. no browser available); only mark resolved when the browser
        // actually opened.
        promptResolved = await vscode.env.openExternal(
            vscode.Uri.parse(
                `https://marketplace.visualstudio.com/items?itemName=${NEW_EXTENSION_ID}`,
            ),
        );
    } else {
        // choice === DISMISS_ACTION or undefined (toast closed without picking)
        // — treat both as an explicit "I've seen this" and don't re-prompt.
        promptResolved = true;
    }

    if (promptResolved) {
        await context.globalState.update(STORAGE_KEY, true);
    }
}

export function deactivate(): void {
    // No-op: this tombstone extension holds no resources.
}
