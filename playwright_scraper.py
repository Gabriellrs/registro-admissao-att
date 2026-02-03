// src/features/certificate/services/apiService.ts

export async function fetchExternalRecord(cpf: string): Promise<any> {
    try {
        const response = await fetch('https://registro-admissao-att.onrender.com/api/buscar-registro', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ cpf }), // Envia o CPF para o serviço externo
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Erro na requisição ao scraper local: ${response.status} - ${errorText}`);
        }

        const data = await response.json();
        console.log('Dados recebidos:', data);
        return data;

    } catch (error) {
        console.error('Falha ao buscar registro de admissão:', error);
        // Dependendo da necessidade, você pode retornar um valor padrão ou relançar o erro
        throw error;
    }
}

export async function fetchExternalRecordByName(nome: string, municipio?: string, cargo?: string): Promise<any> {
    try {
        const payload: any = { nome };
        if (municipio) payload.municipio = municipio;
        if (cargo) payload.cargo = cargo;

        const response = await fetch('https://registro-admissao-att.onrender.com/api/buscar-registro', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload), // Envia Nome e Filtros
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Erro na requisição ao scraper local (Busca por Nome): ${response.status} - ${errorText}`);
        }

        const data = await response.json();
        console.log('Dados recebidos (Busca por Nome):', data);
        return data;

    } catch (error) {
        console.error('Falha ao buscar registro de admissão por nome:', error);
        throw error;
    }
}
