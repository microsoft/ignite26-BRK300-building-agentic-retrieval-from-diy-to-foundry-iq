targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment.')
param environmentName string

@description('Primary Azure region for all resources.')
param location string

@description('Foundry IQ search service SKU.')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'standard'

@description('Principal that provisions resources and runs ingestion. Passing it explicitly lets azd redeploy role assignments when the deploying identity changes.')
param principalId string = ''

var tags = {
  'azd-env-name': environmentName
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources './resources.bicep' = {
  scope: resourceGroup
  params: {
    environmentName: environmentName
    location: location
    principalId: empty(principalId) ? deployer().objectId : principalId
    searchSku: searchSku
    tags: tags
  }
}

output AZURE_RESOURCE_GROUP string = resourceGroup.name
output AZURE_LOCATION string = location
output AZURE_CONTAINER_APP_NAME string = resources.outputs.containerAppName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.containerRegistryEndpoint
output FOUNDRY_IQ_SEARCH_ENDPOINT string = resources.outputs.searchEndpoint
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAiEndpoint
output AZURE_AI_FOUNDRY_ENDPOINT string = resources.outputs.foundryEndpoint
output AZURE_AI_ACCOUNT_NAME string = resources.outputs.aiAccountName
output AZURE_AI_PROJECT_NAME string = resources.outputs.aiProjectName
output AZURE_AI_PROJECT_ID string = resources.outputs.aiProjectId
output AZURE_AI_PROJECT_ENDPOINT string = resources.outputs.aiProjectEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = resources.outputs.aiProjectEndpoint
output FOUNDRY_MODEL_DEPLOYMENT_NAME string = resources.outputs.chatDeploymentName
output FOUNDRY_DECISION_DEPLOYMENT_NAME string = resources.outputs.decisionDeploymentName
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_CONTENT_UNDERSTANDING_DEPLOYMENT string = resources.outputs.contentUnderstandingDeploymentName
output AZURE_CONTENT_UNDERSTANDING_MODEL string = resources.outputs.contentUnderstandingModelName
output AZURE_STORAGE_ACCOUNT_NAME string = resources.outputs.storageAccountName
output AZURE_STORAGE_ACCOUNT_ID string = resources.outputs.storageAccountId
output SERVICE_WEB_URI string = resources.outputs.webUri
